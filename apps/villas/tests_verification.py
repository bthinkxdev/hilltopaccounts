"""Expense lifecycle: created → paid (by staff or owner) → verified as received by the owner."""
from datetime import date, timedelta
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User
from apps.accounts.services import assign_business_manager, assign_villa_staff
from apps.audit.models import Action, AuditLog
from apps.billing.services import create_charge, generate_monthly_invoice, get_or_create_charge_type, record_payment
from apps.businesses.services import create_business
from apps.cash import selectors as cash_selectors
from apps.cash.models import CashHandover
from apps.cash.services import confirm_cash_handover, reject_cash_handover, submit_cash_handover
from apps.expenses.models import Expense, RecurringExpense
from apps.expenses.services import create_expense, create_recurring_expense, get_or_create_category
from apps.notifications.models import Notification
from apps.notifications.services import sync_for_user
from apps.tenancy.services import create_tenant
from apps.villas.models import Villa
from apps.villas.services import create_partition, create_villa

PASSWORD = 'Passw0rd!2026'
TODAY = date.today()

class VerificationBase(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', password=PASSWORD, is_owner=True)
        self.manager = User.objects.create_user('manager', password=PASSWORD)
        self.staff = User.objects.create_user('staff', password=PASSWORD)
        self.other_staff = User.objects.create_user('other_staff', password=PASSWORD)
        self.business = create_business(name='Biz', created_by=self.owner)
        self.other_business = create_business(name='Other', created_by=self.owner)
        self.villa = create_villa(business=self.business, name='Villa A', created_by=self.owner)
        self.other_villa = create_villa(business=self.other_business, name='Villa B', created_by=self.owner)
        assign_business_manager(user=self.manager, business=self.business, assigned_by=self.owner)
        assign_villa_staff(user=self.staff, villa=self.villa, assigned_by=self.owner)
        assign_villa_staff(user=self.other_staff, villa=self.other_villa, assigned_by=self.owner)

    def login(self, username):
        self.client.logout()
        self.assertTrue(self.client.login(username=username, password=PASSWORD))

    def expense(self, name='Maintenance', amount='200.00', paid_by='staff', villa=None, **extra):
        return create_expense(villa=villa or self.villa, category=get_or_create_category(name=name), amount=Decimal(amount), date=TODAY, due_date=TODAY + timedelta(days=3), paid_by=paid_by, created_by=self.owner, **extra)

    def rent_cash(self, amount='1000.00', staff=None, name='U1'):
        staff = staff or self.staff
        partition = create_partition(villa=self.villa, name=name, created_by=self.owner)
        create_tenant(partition=partition, name=f'T {name}', move_in_date=date(2026, 1, 1), monthly_rent=Decimal(amount), created_by=self.owner)
        create_charge(partition=partition, charge_type=get_or_create_charge_type(name='Rent'), amount=Decimal(amount), start_date=date(2026, 1, 1), created_by=self.owner)
        invoice = generate_monthly_invoice(partition=partition, billing_period_start=date(2026, 2, 1), billing_period_end=date(2026, 2, 28), issue_date=date(2026, 2, 1), due_date=date(2030, 1, 1), generated_by=self.owner)
        return record_payment(invoice=invoice, amount=Decimal(amount), method='cash', collected_by=staff, collected_at=TODAY, created_by=staff)

class ExpenseVerificationFlowTests(VerificationBase):

    def test_staff_payment_waits_for_owner_verification(self):
        expense = self.expense()
        self.login('staff')
        r = self.client.post(reverse('expenses:quick_paid', args=[expense.pk]))
        self.assertEqual(r.status_code, 302)
        expense.refresh_from_db()
        self.assertEqual((expense.paid_on, expense.paid_by, expense.paid_by_user, expense.verified_at), (TODAY, 'staff', self.staff, None))
        self.assertTrue(expense.awaiting_verification)
        self.assertEqual(self.client.post(reverse('expenses:verify', args=[expense.pk])).status_code, 403)  # staff cannot verify
        # The owner sees it as "to verify" and confirms with one tap.
        self.login('owner')
        listing = self.client.get(reverse('expenses:list'), {'due': 'verify'})
        self.assertEqual([e.pk for e in listing.context['page_obj']], [expense.pk])
        self.assertContains(listing, 'Verify expenses')
        self.assertEqual(self.client.get(reverse('expenses:verify', args=[expense.pk])).status_code, 405)  # never a GET
        self.assertEqual(self.client.post(reverse('expenses:verify', args=[expense.pk])).status_code, 302)
        expense.refresh_from_db()
        self.assertEqual((expense.verified_by, expense.awaiting_verification), (self.owner, False))
        self.assertTrue(AuditLog.objects.filter(action=Action.EXPENSE_UPDATED, object_id=str(expense.pk), reason='verified as paid and received').exists())
        self.client.post(reverse('expenses:verify', args=[expense.pk]))  # a second tap is refused, not applied twice
        self.assertEqual(AuditLog.objects.filter(object_id=str(expense.pk), reason='verified as paid and received').count(), 1)

    def test_owner_payment_is_final_and_takes_over_staff_expenses(self):
        mine = self.expense(name='Rent', paid_by='owner')
        theirs = self.expense(name='Cleaning', paid_by='staff')
        self.login('staff')
        self.assertEqual(self.client.post(reverse('expenses:quick_paid', args=[mine.pk])).status_code, 403)  # owner's own expense
        self.login('owner')
        for expense in (mine, theirs):
            self.client.post(reverse('expenses:quick_paid', args=[expense.pk]))
            expense.refresh_from_db()
            self.assertEqual((expense.paid_by, expense.paid_by_user, expense.awaiting_verification, expense.is_verified), ('owner', self.owner, False, True))
        self.assertEqual(cash_selectors.villa_cash_position(self.villa)['spent_by_staff'], Decimal('0.00'))  # not staff cash
        # A Business Manager is an admin for their own business only.
        other = self.expense(name='Gas', villa=self.other_villa)
        self.login('manager')
        self.assertEqual(self.client.post(reverse('expenses:quick_paid', args=[other.pk])).status_code, 404)

    def test_created_already_paid_follows_the_same_rule(self):
        self.login('staff')
        self.client.post(reverse('expenses:create_for_villa', args=[self.villa.pk]), {'villa': self.villa.pk, 'name': 'Plumber', 'amount': '75.00', 'date': TODAY.isoformat(), 'paid_by': 'staff', 'mark_paid': 'on', 'payment_method': 'cash'})
        plumber = Expense.objects.get(category__name='Plumber')
        self.assertTrue(plumber.awaiting_verification)
        self.login('owner')
        self.client.post(reverse('expenses:create_for_villa', args=[self.villa.pk]), {'villa': self.villa.pk, 'name': 'Painter', 'amount': '90.00', 'date': TODAY.isoformat(), 'mark_paid': 'on', 'payment_method': 'bank_transfer'})
        self.assertTrue(Expense.objects.get(category__name='Painter').is_verified)

    def test_quick_actions_are_post_only_and_never_redirect_off_site(self):
        expense = self.expense()
        self.login('staff')
        self.assertEqual(self.client.get(reverse('expenses:quick_paid', args=[expense.pk])).status_code, 405)
        r = self.client.post(reverse('expenses:quick_paid', args=[expense.pk]), {'next': 'https://evil.example/phish'})
        self.assertRedirects(r, reverse('expenses:list'))
        other = self.expense(name='Water')
        r = self.client.post(reverse('expenses:quick_paid', args=[other.pk]), {'next': reverse('villas:villa_detail', args=[self.villa.pk])})
        self.assertRedirects(r, reverse('villas:villa_detail', args=[self.villa.pk]))

    def test_confirming_a_handover_verifies_the_expenses_netted_off_it(self):
        payment = self.rent_cash('1000.00')
        spent = self.expense(amount='300.00')
        self.login('staff')
        self.client.post(reverse('expenses:quick_paid', args=[spent.pk]))
        handover = submit_cash_handover(staff=self.staff, payments=[payment], expenses=[Expense.objects.get(pk=spent.pk)], submitted_by=self.staff)
        self.assertEqual(handover.declared_amount, Decimal('700.00'))
        self.assertTrue(Expense.objects.get(pk=spent.pk).awaiting_verification)
        # Rejected: the expense is still waiting, and the staff can try again.
        reject_cash_handover(handover=handover, rejected_by=self.owner, reason='recount')
        self.assertTrue(Expense.objects.get(pk=spent.pk).awaiting_verification)
        again = submit_cash_handover(staff=self.staff, payments=[payment], expenses=[Expense.objects.get(pk=spent.pk)], submitted_by=self.staff)
        confirm_cash_handover(handover=again, confirmed_amount=Decimal('700.00'), confirmed_by=self.owner)
        spent.refresh_from_db()
        self.assertEqual((spent.verified_by, spent.awaiting_verification), (self.owner, False))
        self.assertTrue(AuditLog.objects.filter(object_id=str(spent.pk), reason__startswith='verified by confirming cash handover').exists())

class VillaMoneyViewTests(VerificationBase):

    def test_villa_page_splits_expenses_by_payer_and_tracks_cash(self):
        payment = self.rent_cash('1000.00')
        staff_paid = self.expense(name='Maintenance', amount='150.00', paid_by='staff')
        owner_paid = self.expense(name='Rent', amount='400.00', paid_by='owner')
        unpaid = self.expense(name='Wifi', amount='50.00', paid_by='staff')
        self.login('staff')
        self.client.post(reverse('expenses:quick_paid', args=[staff_paid.pk]))
        self.login('owner')
        self.client.post(reverse('expenses:quick_paid', args=[owner_paid.pk]))
        ctx = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), {'period': 'month', 'year': TODAY.year, 'month': TODAY.month}).context
        pnl, cash = ctx['pnl'], ctx['cash']
        self.assertEqual((pnl['staff_expenses'], pnl['owner_expenses'], pnl['expenses'], pnl['to_verify'], pnl['unpaid_expenses']), (Decimal('200.00'), Decimal('400.00'), Decimal('600.00'), Decimal('150.00'), Decimal('50.00')))
        self.assertEqual((cash['collected'], cash['spent_by_staff'], cash['received_by_owner'], cash['holding']), (Decimal('1000.00'), Decimal('150.00'), Decimal('0.00'), Decimal('850.00')))
        # Hand over 850, owner confirms: nothing is left with the staff.
        handover = submit_cash_handover(staff=self.staff, payments=[payment], expenses=[Expense.objects.get(pk=staff_paid.pk)], submitted_by=self.staff)
        cash = cash_selectors.villa_cash_position(self.villa)
        self.assertEqual((cash['awaiting_confirmation'], cash['holding']), (Decimal('850.00'), Decimal('850.00')))
        confirm_cash_handover(handover=handover, confirmed_amount=Decimal('850.00'), confirmed_by=self.owner)
        cash = cash_selectors.villa_cash_position(self.villa)
        self.assertEqual((cash['received_by_owner'], cash['awaiting_confirmation'], cash['holding']), (Decimal('850.00'), Decimal('0.00'), Decimal('0.00')))

    def test_staff_see_their_villas_figures_but_never_partner_shares(self):
        self.rent_cash('1000.00')
        from apps.businesses.models import Partner, PartnerVillaShare
        PartnerVillaShare.objects.create(partner=Partner.objects.create(name='Silent Partner', created_by=self.owner), villa=self.villa, percentage=Decimal('30.00'))
        self.login('staff')
        r = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]))
        self.assertIn('cash', r.context)
        self.assertIn('pnl', r.context)  # income / expenses / profit of their own villa
        self.assertNotContains(r, 'Silent Partner')
        self.assertNotContains(r, 'Profit Sharing Breakdown')
        home = self.client.get(reverse('dashboard:home'))
        self.assertIsNotNone(home.context['period_pnl'])  # same figures on their dashboard, their villas only
        self.assertNotContains(home, 'Financial summary')
        self.assertNotContains(home, 'Silent Partner')
        self.assertNotContains(r, 'Income, expenses')

    def test_villa_page_has_no_section_tab_bar(self):
        self.login('owner')
        r = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]))
        self.assertNotContains(r, 'section-nav')

class VerificationNotificationTests(VerificationBase):

    def test_owner_and_business_manager_are_told_but_other_staff_are_not(self):
        expense = self.expense()
        self.login('staff')
        self.client.post(reverse('expenses:quick_paid', args=[expense.pk]))
        kinds = lambda user: (sync_for_user(user), set(Notification.objects.filter(recipient=user, resolved_at__isnull=True).values_list('kind', flat=True)))[1]
        self.assertIn(Notification.Kind.EXPENSE_TO_VERIFY, kinds(self.owner))
        self.assertIn(Notification.Kind.EXPENSE_TO_VERIFY, kinds(self.manager))
        self.assertNotIn(Notification.Kind.EXPENSE_TO_VERIFY, kinds(self.staff))
        self.assertNotIn(Notification.Kind.EXPENSE_TO_VERIFY, kinds(self.other_staff))
        self.login('owner')
        self.client.post(reverse('expenses:verify', args=[expense.pk]))
        self.assertNotIn(Notification.Kind.EXPENSE_TO_VERIFY, kinds(self.owner))

class VillaCreationDefaultsTests(VerificationBase):

    def _post(self, **fixed):
        data = {'business': self.business.pk, 'name': 'New Villa', 'address': 'Somewhere'}
        data.update(fixed)
        return self.client.post(reverse('villas:villa_create'), data)

    def test_default_fixed_expenses_are_optional_and_appear_every_month(self):
        self.login('owner')
        r = self._post(fixed_villa_rent='9500', fixed_electricity='600.50', fixed_wifi='', fixed_cleaning='', fixed_maintenance='135')
        villa = Villa.objects.get(name='New Villa')
        self.assertRedirects(r, reverse('villas:villa_detail', args=[villa.pk]))
        fixed = {f.category.name: (f.amount, f.paid_by) for f in RecurringExpense.objects.filter(villa=villa)}
        self.assertEqual(fixed, {'Villa rent': (Decimal('9500.00'), 'owner'), 'Electricity': (Decimal('600.50'), 'staff'), 'Company Service': (Decimal('135.00'), 'staff')})
        page = self.client.get(reverse('villas:villa_detail', args=[villa.pk]))
        self.assertEqual({row['template'].category.name: row['status'] for row in page.context['fixed_rows']}, {'Villa rent': 'overdue' if TODAY.day > 1 else 'due_today', 'Electricity': 'overdue' if TODAY.day > 1 else 'due_today', 'Company Service': 'overdue' if TODAY.day > 1 else 'due_today'})
        self.assertEqual(Expense.objects.filter(villa=villa).count(), 3)

    def test_all_blank_creates_a_villa_with_no_fixed_expenses_and_the_owner_can_add_later(self):
        self.login('owner')
        self._post()
        villa = Villa.objects.get(name='New Villa')
        self.assertFalse(RecurringExpense.objects.filter(villa=villa).exists())
        r = self.client.post(reverse('expenses:recurring_create', args=[villa.pk]), {'name': 'Cleaning', 'amount': '200', 'due_day': 5, 'paid_by': 'staff'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(RecurringExpense.objects.get(villa=villa).amount, Decimal('200.00'))

    def test_bad_amount_is_a_field_error_and_creates_nothing(self):
        self.login('owner')
        r = self._post(fixed_electricity='-5')
        self.assertEqual(r.status_code, 200)
        self.assertIn('fixed_electricity', r.context['form'].errors)
        self.assertFalse(Villa.objects.filter(name='New Villa').exists())

    def test_a_business_manager_can_set_defaults_for_their_own_business(self):
        self.login('manager')
        self._post(fixed_wifi='300')
        villa = Villa.objects.get(name='New Villa')
        self.assertEqual(RecurringExpense.objects.get(villa=villa).category.name, 'Wi-Fi')


class FixedExpenseAssignmentTests(VerificationBase):
    """The Admin chooses, per monthly expense, whether the Admin or the Staff pays it; staff can only view the Admin's."""

    def _create(self, **fixed):
        data = {'business': self.business.pk, 'name': 'Assigned Villa', 'address': 'x'}
        data.update(fixed)
        return self.client.post(reverse('villas:villa_create'), data)

    def _paid_by(self, villa):
        return {f.category.name: f.assigned_to for f in RecurringExpense.objects.filter(villa=villa)}

    def test_admin_chooses_who_pays_each_monthly_expense(self):
        self.login('owner')
        self._create(fixed_villa_rent='9000', fixed_villa_rent_assigned='staff', fixed_electricity='600', fixed_electricity_assigned='owner', fixed_wifi='50')
        villa = Villa.objects.get(name='Assigned Villa')
        # untouched choices keep the old defaults (rent: Admin, others: Staff)
        self.assertEqual(self._paid_by(villa), {'Villa rent': 'staff', 'Electricity': 'owner', 'Wi-Fi': 'staff'})
        self.assertEqual({e.category.name: e.assigned_to for e in Expense.objects.filter(villa=villa)}, {'Villa rent': 'staff', 'Electricity': 'owner', 'Wi-Fi': 'staff'})

    def test_the_choice_is_shown_to_the_admin_only(self):
        self.login('owner')
        self.assertIn('fixed_electricity_assigned', self.client.get(reverse('villas:villa_create')).content.decode())
        self.login('manager')
        self.assertNotIn('fixed_electricity_assigned', self.client.get(reverse('villas:villa_create')).content.decode())

    def test_a_non_admin_cannot_set_the_choice(self):
        self.login('manager')
        self._create(fixed_villa_rent='9000', fixed_villa_rent_assigned='staff', fixed_electricity='600', fixed_electricity_assigned='owner')
        villa = Villa.objects.get(name='Assigned Villa')
        self.assertEqual(self._paid_by(villa), {'Villa rent': 'owner', 'Electricity': 'staff'})  # the defaults

    def test_admin_can_change_the_assignment_when_editing_the_villa(self):
        self.login('owner')
        self._create(fixed_electricity='600')
        villa = Villa.objects.get(name='Assigned Villa')
        page = self.client.get(reverse('villas:villa_edit', args=[villa.pk]))
        self.assertEqual(page.context['form'].initial['fixed_electricity_assigned'], 'staff')
        r = self.client.post(reverse('villas:villa_edit', args=[villa.pk]), {'name': villa.name, 'fixed_electricity': '600', 'fixed_electricity_assigned': 'owner'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self._paid_by(villa), {'Electricity': 'owner'})

    def test_staff_cannot_pay_an_admin_assigned_monthly_expense_but_can_pay_a_staff_one(self):
        self.login('owner')
        self._create(fixed_electricity='600', fixed_electricity_assigned='owner', fixed_wifi='50', fixed_wifi_assigned='staff')
        villa = Villa.objects.get(name='Assigned Villa')
        assign_villa_staff(user=self.staff, villa=villa, assigned_by=self.owner)
        admin_row = Expense.objects.get(villa=villa, category__name='Electricity')
        staff_row = Expense.objects.get(villa=villa, category__name='Wi-Fi')
        self.login('staff')
        self.client.post(reverse('expenses:quick_paid', args=[admin_row.pk]))
        admin_row.refresh_from_db()
        self.assertIsNone(admin_row.paid_on)
        self.client.post(reverse('expenses:quick_paid', args=[staff_row.pk]))
        staff_row.refresh_from_db()
        self.assertIsNotNone(staff_row.paid_on)
        # the villa page shows both cards to staff; the Admin one has no tick box (read-only, status only)
        page = self.client.get(reverse('villas:villa_detail', args=[villa.pk]))
        html = page.content.decode()
        self.assertIn('Admin Expenses', html)
        self.assertNotIn(f'name="expense_ids" value="{admin_row.pk}"', html)
        self.assertTrue(all(not r['expense'].can_pay for r in page.context['owner_fixed_rows']))

    def test_the_admin_can_still_pay_both(self):
        self.login('owner')
        self._create(fixed_electricity='600', fixed_electricity_assigned='owner', fixed_wifi='50', fixed_wifi_assigned='staff')
        villa = Villa.objects.get(name='Assigned Villa')
        for row in Expense.objects.filter(villa=villa):
            self.client.post(reverse('expenses:quick_paid', args=[row.pk]))
            row.refresh_from_db()
            self.assertIsNotNone(row.paid_on)


class ReviseAssignmentTests(VerificationBase):
    """The Admin can move an unpaid expense between Admin and Staff from its edit popup."""

    def setUp(self):
        super().setUp()
        self.row = self.expense(name='Electricity', paid_by='staff')
        self.url = reverse('expenses:revise', args=[self.row.pk])

    def test_the_admin_sees_the_choice_and_can_change_it(self):
        self.login('owner')
        page = self.client.get(self.url)
        self.assertEqual(page.context['form'].initial['assigned_to'], 'staff')
        r = self.client.post(self.url, {'amount': '210.00', 'assigned_to': 'owner', 'description': ''})
        self.assertEqual(r.status_code, 302)
        self.row.refresh_from_db()
        self.assertEqual((self.row.assigned_to, self.row.amount), ('owner', Decimal('210.00')))

    def test_leaving_it_blank_keeps_the_assignment(self):
        self.login('owner')
        self.client.post(self.url, {'amount': '220.00', 'assigned_to': '', 'description': ''})
        self.row.refresh_from_db()
        self.assertEqual((self.row.assigned_to, self.row.amount), ('staff', Decimal('220.00')))

    def test_staff_do_not_see_the_choice_and_cannot_set_it(self):
        self.login('staff')
        self.assertNotIn('name="assigned_to"', self.client.get(self.url).content.decode())
        self.client.post(self.url, {'amount': '230.00', 'assigned_to': 'owner', 'description': ''})
        self.row.refresh_from_db()
        self.assertEqual((self.row.assigned_to, self.row.amount), ('staff', Decimal('230.00')))

    def test_an_auto_debit_stays_with_the_admin(self):
        rec = create_recurring_expense(villa=self.villa, category=get_or_create_category(name='Rent'), amount=Decimal('900'), due_day=1, auto_debit=True, created_by=self.owner, start_period=TODAY.replace(day=1))
        row = Expense.objects.get(recurring=rec)
        self.login('owner')
        self.client.post(reverse('expenses:revise', args=[row.pk]), {'amount': '900.00', 'assigned_to': 'staff', 'description': ''})
        row.refresh_from_db()
        self.assertEqual(row.assigned_to, 'owner')


class AssignedVersusPaidByTests(VerificationBase):
    """'Assigned to' is who is going to pay; 'paid by' stays the record of who actually paid."""

    def test_assigning_does_not_touch_paid_by_and_paying_records_the_real_payer(self):
        self.login('owner')
        self.client.post(reverse('villas:villa_create'), {'business': self.business.pk, 'name': 'AP Villa', 'fixed_wifi': '50', 'fixed_wifi_assigned': 'staff', 'fixed_villa_rent': '900', 'fixed_villa_rent_assigned': 'staff'})
        villa = Villa.objects.get(name='AP Villa')
        wifi = Expense.objects.get(villa=villa, category__name='Wi-Fi')
        self.assertEqual((wifi.assigned_to, wifi.paid_by), ('staff', 'staff'))
        rent = Expense.objects.get(villa=villa, category__name='Villa rent')
        self.assertEqual((rent.assigned_to, rent.paid_by), ('staff', 'owner'))  # paid_by keeps its own default
        self.client.post(reverse('expenses:quick_paid', args=[wifi.pk]))  # the admin pays what was assigned to staff
        wifi.refresh_from_db()
        self.assertEqual((wifi.assigned_to, wifi.paid_by, wifi.paid_by_user), ('staff', 'owner', self.owner))
        html = self.client.get(reverse('villas:villa_detail', args=[villa.pk])).content.decode()
        self.assertIn('Paid by Admin — scheduled as Staff', html)

    def test_the_add_expense_form_assigns_for_the_admin_only(self):
        url = reverse('expenses:create_for_villa', args=[self.villa.pk])
        self.login('owner')
        self.assertIn('name="assigned_to"', self.client.get(url).content.decode())
        data = {'villa': self.villa.pk, 'name': 'Gas', 'amount': '40.00', 'date': TODAY.isoformat(), 'frequency': 'one_time', 'assigned_to': 'owner'}
        self.client.post(url, data)
        self.assertEqual(Expense.objects.get(category__name='Gas').assigned_to, 'owner')
        self.login('staff')
        self.assertNotIn('name="assigned_to"', self.client.get(url).content.decode())
        self.client.post(url, {**data, 'name': 'Soap', 'assigned_to': 'owner'})
        self.assertEqual(Expense.objects.get(category__name='Soap').assigned_to, 'staff')

    def test_a_monthly_expense_can_be_assigned_when_added_and_edited(self):
        self.login('owner')
        self.client.post(reverse('expenses:create_for_villa', args=[self.villa.pk]), {'villa': self.villa.pk, 'name': 'Pest control', 'amount': '90.00', 'date': TODAY.replace(day=1).isoformat(), 'frequency': 'monthly', 'assigned_to': 'owner'})
        rec = RecurringExpense.objects.get(category__name='Pest control')
        self.assertEqual(rec.assigned_to, 'owner')
        row = Expense.objects.get(recurring=rec)
        self.assertEqual(row.assigned_to, 'owner')
        self.client.post(reverse('expenses:recurring_edit', args=[rec.pk]), {'name': 'Pest control', 'amount': '90.00', 'assigned_to': 'staff'})
        rec.refresh_from_db(); row.refresh_from_db()
        self.assertEqual((rec.assigned_to, row.assigned_to), ('staff', 'staff'))  # this month's unpaid row follows
