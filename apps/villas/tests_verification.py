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
from apps.expenses.services import create_expense, get_or_create_category
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
        self.assertContains(listing, 'Verify received')
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

    def test_staff_see_the_cash_card_but_never_profit(self):
        self.rent_cash('1000.00')
        self.login('staff')
        r = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]))
        self.assertIn('cash', r.context)
        self.assertNotIn('pnl', r.context)
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
        self.assertEqual(fixed, {'Villa rent': (Decimal('9500.00'), 'owner'), 'Electricity': (Decimal('600.50'), 'staff'), 'Maintenance': (Decimal('135.00'), 'staff')})
        page = self.client.get(reverse('villas:villa_detail', args=[villa.pk]))
        self.assertEqual({row['template'].category.name: row['status'] for row in page.context['fixed_rows']}, {'Villa rent': 'overdue' if TODAY.day > 1 else 'due_today', 'Electricity': 'overdue' if TODAY.day > 1 else 'due_today', 'Maintenance': 'overdue' if TODAY.day > 1 else 'due_today'})
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
