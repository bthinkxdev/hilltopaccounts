"""The day-to-day field workflow: staff collect rent, pay some expenses from that cash, hand the rest to the owner.

Numbers mirror the owner's notebook for Villa 98: rents 16,850, expenses 12,350, profit 4,500.
"""
from datetime import date
from decimal import Decimal
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User
from apps.accounts.services import assign_business_manager, assign_villa_staff
from apps.audit.models import Action, AuditLog
from apps.billing.models import Invoice, Payment
from apps.businesses.services import create_business
from apps.cash.exceptions import InvalidHandoverPayment
from apps.cash.models import CashHandover
from apps.cash.services import submit_cash_handover
from apps.expenses.models import Expense, RecurringExpense
from apps.notifications.models import Notification
from apps.notifications.services import sync_for_user
from apps.tenancy.services import create_tenant
from apps.villas.services import create_partition, create_villa

PASSWORD = 'Passw0rd!2026'
RENTS = [('Outhouse', 2400), ('Ground Studio 1', 1900), ('Ground Studio 2', 1700), ('Ground Studio 3', 2700), ('First Studio 1', 1800), ('First Studio 2', 1900), ('First 1BHK', 2600), ('Pent house', 1850)]
FIXED = [('Villa owner rent', '9500.00', 'owner', True), ('Kharama', '2000.00', 'staff', False), ('Wifi', '415.00', 'staff', False), ('Maintenance', '135.00', 'staff', False), ('Villa service', '300.00', 'owner', False)]
YEAR, MONTH = date.today().year, date.today().month

class FieldworkBase(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', password=PASSWORD, is_owner=True)
        self.staff = User.objects.create_user('staff', password=PASSWORD)
        self.other_staff = User.objects.create_user('other_staff', password=PASSWORD)
        self.manager = User.objects.create_user('manager', password=PASSWORD)
        self.business = create_business(name='Kabayan Properties', created_by=self.owner)
        self.other_business = create_business(name='Other Co', created_by=self.owner)
        self.villa = create_villa(business=self.business, name='Villa 98 - Kabayan', created_by=self.owner)
        self.other_villa = create_villa(business=self.other_business, name='Villa 12', created_by=self.owner)
        assign_villa_staff(user=self.staff, villa=self.villa, assigned_by=self.owner)
        assign_villa_staff(user=self.other_staff, villa=self.other_villa, assigned_by=self.owner)
        assign_business_manager(user=self.manager, business=self.business, assigned_by=self.owner)
        self.partitions = []
        for name, rent in RENTS:
            partition = create_partition(villa=self.villa, name=name, created_by=self.owner)
            create_tenant(partition=partition, name=f'Tenant {name}', move_in_date=date(2026, 1, 1), monthly_rent=Decimal(rent), created_by=self.owner)
            self.partitions.append(partition)
        self.period = {'period': 'month', 'year': YEAR, 'month': MONTH}

    def login(self, username):
        self.client.logout()
        self.assertTrue(self.client.login(username=username, password=PASSWORD))

    def set_up_fixed_expenses(self):
        self.login('owner')
        for name, amount, paid_by, auto_debit in FIXED:
            data = {'name': name, 'amount': amount, 'due_day': 1, 'paid_by': paid_by}
            if auto_debit:
                data['auto_debit'] = 'on'
            r = self.client.post(reverse('expenses:recurring_create', args=[self.villa.pk]), data)
            self.assertEqual(r.status_code, 302, (name, getattr(r, 'context', None) and r.context['form'].errors))

    def generate_month(self, username='staff'):
        """Fixed expenses are created automatically when anyone opens the villa for a month."""
        self.login(username)
        return self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), self.period)

    def collect(self, partition, method='cash'):
        return self.client.post(reverse('billing:rent_collect', args=[partition.pk]), {'year': YEAR, 'month': MONTH, 'method': method})

class NotebookScenarioTests(FieldworkBase):

    def test_villa_98_month_end_to_end(self):
        # Owner sets the villa's fixed expenses once.
        self.set_up_fixed_expenses()
        self.assertEqual(RecurringExpense.objects.filter(villa=self.villa).count(), 5)

        # Staff create the month's expenses with one tap; repeating it changes nothing.
        r = self.generate_month('staff')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Expense.objects.filter(villa=self.villa).count(), 5)
        self.generate_month('staff')
        self.assertEqual(Expense.objects.filter(villa=self.villa).count(), 5)

        # Staff collect every partition's rent with one tap each.
        self.login('staff')
        sheet = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), self.period).context
        self.assertEqual(sheet['rent_totals']['expected'], Decimal('16850'))
        self.assertEqual(sheet['rent_totals']['pending_count'], 8)
        for partition in self.partitions:
            self.assertEqual(self.collect(partition).status_code, 302)
        self.assertEqual(Payment.objects.filter(collected_by=self.staff, method='cash').count(), 8)
        sheet = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), self.period).context
        self.assertEqual((sheet['rent_totals']['collected'], sheet['rent_totals']['pending']), (Decimal('16850.00'), Decimal('0.00')))
        self.assertTrue(all(row['status'] == 'collected' for row in sheet['rent_rows']))

        # Staff pay the staff-side expenses from that cash, but cannot settle the owner's own.
        staff_side = Expense.objects.filter(villa=self.villa, paid_by='staff')
        owner_side = Expense.objects.filter(villa=self.villa, paid_by='owner')
        self.assertEqual((staff_side.count(), owner_side.count()), (3, 2))
        for expense in staff_side:
            r = self.client.post(reverse('expenses:mark_paid', args=[expense.pk]), {'paid_on': '2026-02-03', 'payment_method': 'cash'})
            self.assertEqual(r.status_code, 302)
        # The bank debited the owner's rent on its due date (already past), so it settled itself at generation.
        rent = Expense.objects.get(recurring__category__name='Villa owner rent')
        self.assertEqual((rent.paid_on, rent.payment_method), (date(YEAR, MONTH, 1), 'bank_transfer'))
        self.assertTrue(AuditLog.objects.filter(action=Action.EXPENSE_UPDATED, object_id=str(rent.pk), reason='auto-debit from owner account').exists())
        from apps.expenses.services import settle_auto_debits
        self.assertEqual(settle_auto_debits(today=date(2026, 3, 1)), 0)
        # Staff cannot settle an expense the owner pays.
        service = Expense.objects.get(recurring__category__name='Villa service')
        self.assertEqual(self.client.post(reverse('expenses:mark_paid', args=[service.pk]), {'paid_on': '2026-02-03', 'payment_method': 'cash'}).status_code, 403)
        self.assertIsNone(Expense.objects.get(pk=service.pk).paid_on)
        self.login('owner')
        self.assertEqual(self.client.post(reverse('expenses:mark_paid', args=[service.pk]), {'paid_on': '2026-02-04', 'payment_method': 'bank_transfer'}).status_code, 302)

        # Income 16,850 - expenses 12,350 = profit 4,500, straight from the financial engine.
        pnl = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), self.period).context['pnl']
        self.assertEqual((pnl['income'], pnl['expenses'], pnl['net']), (Decimal('16850.00'), Decimal('12350.00'), Decimal('4500.00')))
        row = [v for v in self.client.get(reverse('villas:villa_list'), self.period).context['page_obj'] if v.pk == self.villa.pk][0]
        self.assertEqual(row.pnl['net'], Decimal('4500.00'))

        # Staff hand the cash over net of what they spent: 16,850 - 2,550 = 14,300.
        self.login('staff')
        from apps.cash import selectors as cash_selectors
        self.assertEqual(cash_selectors.outstanding_cash_for(self.staff), Decimal('14300.00'))
        form_page = self.client.get(reverse('cash:handover_submit'))
        self.assertEqual(form_page.status_code, 200)
        payment_ids = list(Payment.objects.filter(collected_by=self.staff).values_list('pk', flat=True))
        r = self.client.post(reverse('cash:handover_submit'), {'payments': payment_ids, 'expenses': list(staff_side.values_list('pk', flat=True))})
        handover = CashHandover.objects.get(staff=self.staff)
        self.assertRedirects(r, reverse('cash:handover_detail', args=[handover.pk]))
        self.assertEqual(handover.declared_amount, Decimal('14300.00'))
        self.assertEqual(handover.expenses.count(), 3)
        self.assertContains(self.client.get(reverse('cash:handover_detail', args=[handover.pk])), 'Kharama')

        # The owner is told, sees it on the villa card, and confirms.
        sync_for_user(self.owner)
        self.assertTrue(Notification.objects.filter(recipient=self.owner, kind=Notification.Kind.CASH_HANDOVER_PENDING).exists())
        self.login('owner')
        card = [v for v in self.client.get(reverse('villas:villa_list'), self.period).context['page_obj'] if v.pk == self.villa.pk][0]
        self.assertEqual(card.unconfirmed_cash, Decimal('16850.00'))
        acct = self.client.get(reverse('cash:staff_accountability'), self.period).context['rows']
        staff_row = [r for r in acct if r['staff'] == self.staff][0]
        self.assertEqual((staff_row['collected'], staff_row['expenses_paid'], staff_row['awaiting_confirmation'], staff_row['holding']), (Decimal('16850.00'), Decimal('2550.00'), Decimal('14300.00'), Decimal('14300.00')))
        r = self.client.post(reverse('cash:handover_confirm', args=[handover.pk]), {'confirmed_amount': '14300.00'})
        self.assertRedirects(r, reverse('cash:handover_detail', args=[handover.pk]))
        staff_row = [r for r in self.client.get(reverse('cash:staff_accountability'), self.period).context['rows'] if r['staff'] == self.staff][0]
        self.assertEqual((staff_row['handed_over'], staff_row['holding'], staff_row['pending_rent_count']), (Decimal('14300.00'), Decimal('0.00'), 0))

        # Everything important left an audit trail.
        for action in (Action.INVOICE_CREATED, Action.COLLECTION_CREATED, Action.EXPENSE_CREATED, Action.EXPENSE_UPDATED, Action.CASH_HANDOVER_SUBMITTED, Action.CASH_HANDOVER_CONFIRMED):
            self.assertTrue(AuditLog.objects.filter(action=action).exists(), action)

class RentCollectionTests(FieldworkBase):

    def test_double_tap_collects_once_and_partial_status_is_shown(self):
        self.login('staff')
        partition = self.partitions[0]
        self.assertEqual(self.collect(partition).status_code, 302)
        self.collect(partition)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Invoice.objects.filter(partition=partition).count(), 1)
        rows = {r['partition'].pk: r['status'] for r in self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), self.period).context['rent_rows']}
        self.assertEqual(rows[partition.pk], 'collected')
        self.assertEqual(rows[self.partitions[1].pk], 'pending')

    def test_only_the_villas_own_staff_can_collect(self):
        self.login('other_staff')
        self.assertEqual(self.collect(self.partitions[0]).status_code, 404)
        self.login('staff')
        self.assertEqual(self.client.get(reverse('billing:rent_collect', args=[self.partitions[0].pk])).status_code, 405)
        self.assertEqual(self.client.post(reverse('billing:rent_collect', args=[self.partitions[0].pk]), {'year': 'x', 'month': 'y'}).status_code, 302)
        self.assertEqual(self.client.post(reverse('billing:rent_collect', args=[self.partitions[0].pk]), {'year': YEAR, 'month': MONTH, 'method': 'crypto'}).status_code, 302)
        self.assertEqual(Payment.objects.count(), 0)

    def test_vacant_partition_is_not_billed(self):
        from apps.tenancy.services import move_out_tenant
        partition = self.partitions[0]
        move_out_tenant(tenant=partition.current_tenant, move_out_date=date(2026, 1, 31), moved_out_by=self.owner, reason='left')
        self.login('staff')
        self.collect(partition)
        self.assertEqual(Payment.objects.count(), 0)

    def test_pending_rent_shows_on_the_villa_card_and_staff_filter_works(self):
        self.login('staff')
        for partition in self.partitions[:6]:
            self.collect(partition)
        self.login('owner')
        page = self.client.get(reverse('villas:villa_list'), self.period).context
        card = [v for v in page['page_obj'] if v.pk == self.villa.pk][0]
        self.assertEqual(card.pending_rent, 2)
        self.assertEqual(card.staff_names, ['staff'])
        filtered = self.client.get(reverse('villas:villa_list'), {**self.period, 'staff': self.staff.pk}).context['page_obj']
        self.assertEqual([v.pk for v in filtered], [self.villa.pk])
        filtered = self.client.get(reverse('villas:villa_list'), {**self.period, 'staff': self.other_staff.pk}).context['page_obj']
        self.assertEqual([v.pk for v in filtered], [self.other_villa.pk])
        # A staff member has no staff filter and sees no money or other staff names.
        self.login('staff')
        page = self.client.get(reverse('villas:villa_list'), self.period).context
        self.assertEqual(page['staff_choices'], [])
        self.assertIsNone(page['totals'])

    def test_business_manager_sees_only_their_businesss_staff(self):
        self.login('manager')
        page = self.client.get(reverse('villas:villa_list'), self.period).context
        self.assertEqual([u.username for u in page['staff_choices']], ['staff'])
        rows = self.client.get(reverse('cash:staff_accountability'), self.period).context['rows']
        self.assertEqual([r['staff'].username for r in rows], ['staff'])
        self.login('staff')
        self.assertEqual(self.client.get(reverse('cash:staff_accountability')).status_code, 403)

class FixedExpenseAndHandoverRuleTests(FieldworkBase):

    def test_staff_cannot_define_fixed_expenses_and_owner_can_revise_a_month(self):
        self.login('staff')
        self.assertEqual(self.client.post(reverse('expenses:recurring_create', args=[self.villa.pk]), {'name': 'Wifi', 'amount': '1', 'due_day': 1, 'paid_by': 'staff'}).status_code, 403)
        self.set_up_fixed_expenses()
        self.generate_month('staff')
        kharama = Expense.objects.get(recurring__category__name='Kharama')
        self.login('staff')
        r = self.client.post(reverse('expenses:revise', args=[kharama.pk]), {'amount': '2210.50', 'due_date': '2026-02-05', 'description': 'Feb bill'})
        self.assertEqual(r.status_code, 302)
        kharama.refresh_from_db()
        self.assertEqual(kharama.amount, Decimal('2210.50'))
        self.assertEqual(RecurringExpense.objects.get(category__name='Kharama').amount, Decimal('2000.00'))  # template untouched
        self.client.post(reverse('expenses:mark_paid', args=[kharama.pk]), {'paid_on': '2026-02-05', 'payment_method': 'cash'})
        r = self.client.post(reverse('expenses:revise', args=[kharama.pk]), {'amount': '1.00'})
        self.assertEqual(r.status_code, 200)  # paid expenses are history — not editable
        kharama.refresh_from_db()
        self.assertEqual(kharama.amount, Decimal('2210.50'))

    def test_a_changed_template_applies_only_to_months_not_yet_generated(self):
        self.set_up_fixed_expenses()
        self.generate_month('owner')
        wifi = RecurringExpense.objects.get(category__name='Wifi')
        self.login('owner')
        self.client.post(reverse('expenses:recurring_edit', args=[wifi.pk]), {'name': 'Wifi', 'amount': '450.00', 'due_day': 5, 'paid_by': 'staff'})
        self.assertEqual(Expense.objects.get(recurring=wifi, period=date(YEAR, MONTH, 1)).amount, Decimal('415.00'))
        prev_year, prev_month = (YEAR - 1, 12) if MONTH == 1 else (YEAR, MONTH - 1)
        self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), {'period': 'month', 'year': prev_year, 'month': prev_month})
        self.assertEqual(Expense.objects.get(recurring=wifi, period=date(prev_year, prev_month, 1)).amount, Decimal('450.00'))
        self.client.post(reverse('expenses:recurring_stop', args=[wifi.pk]), {'reason': 'cancelled wifi'})
        earlier_year, earlier_month = (prev_year - 1, 12) if prev_month == 1 else (prev_year, prev_month - 1)
        self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), {'period': 'month', 'year': earlier_year, 'month': earlier_month})
        self.assertFalse(Expense.objects.filter(recurring=wifi, period=date(earlier_year, earlier_month, 1)).exists())

    def test_scheduled_command_generates_and_settles(self):
        self.set_up_fixed_expenses()
        call_command('run_monthly_expenses')
        today = date.today().replace(day=1)
        self.assertEqual(Expense.objects.filter(villa=self.villa, period=today).count(), 5)
        call_command('run_monthly_expenses')
        self.assertEqual(Expense.objects.filter(villa=self.villa, period=today).count(), 5)
        self.assertIsNotNone(Expense.objects.get(villa=self.villa, recurring__auto_debit=True, period=today).paid_on)

    def test_handover_is_locked_down(self):
        self.set_up_fixed_expenses()
        self.generate_month('staff')
        self.login('staff')
        for partition in self.partitions[:2]:
            self.collect(partition)
        kharama = Expense.objects.get(recurring__category__name='Kharama')
        self.client.post(reverse('expenses:mark_paid', args=[kharama.pk]), {'paid_on': '2026-02-03', 'payment_method': 'cash'})
        payments = list(Payment.objects.filter(collected_by=self.staff))
        # An expense the owner paid cannot be claimed against the staff's cash.
        owner_rent = Expense.objects.get(recurring__category__name='Villa service')
        self.login('owner')
        self.client.post(reverse('expenses:mark_paid', args=[owner_rent.pk]), {'paid_on': '2026-02-03', 'payment_method': 'cash'})
        with self.assertRaises(InvalidHandoverPayment):
            submit_cash_handover(staff=self.staff, payments=payments, expenses=[Expense.objects.get(pk=owner_rent.pk)], submitted_by=self.staff)
        # Expenses larger than the cash selected are rejected, and nothing is locked by the failed attempt.
        big = Expense.objects.get(pk=kharama.pk)
        Expense.objects.filter(pk=big.pk).update(amount=Decimal('99999.00'))
        from apps.shared.exceptions import DomainError
        with self.assertRaises(DomainError):
            submit_cash_handover(staff=self.staff, payments=payments, expenses=[Expense.objects.get(pk=big.pk)], submitted_by=self.staff)
        self.assertEqual(CashHandover.objects.count(), 0)
        Expense.objects.filter(pk=big.pk).update(amount=Decimal('2000.00'))
        first = submit_cash_handover(staff=self.staff, payments=payments, expenses=[Expense.objects.get(pk=big.pk)], submitted_by=self.staff)
        self.assertEqual(first.declared_amount, Decimal('2300.00'))
        # The same expense cannot be settled in a second handover, even alongside fresh collections.
        self.login('staff')
        self.collect(self.partitions[2])
        self.collect(self.partitions[3])
        fresh = list(Payment.objects.filter(collected_by=self.staff).exclude(handovers__status='submitted'))
        self.assertEqual(len(fresh), 2)
        with self.assertRaises(InvalidHandoverPayment):
            submit_cash_handover(staff=self.staff, payments=fresh, expenses=[big], submitted_by=self.staff)
        # A rejected handover frees its expense and collections for resubmission.
        from apps.cash.services import reject_cash_handover
        reject_cash_handover(handover=first, rejected_by=self.owner, reason='recount')
        again = submit_cash_handover(staff=self.staff, payments=payments, expenses=[big], submitted_by=self.staff)
        self.assertEqual(again.declared_amount, Decimal('2300.00'))


class FieldworkQueryCountTests(FieldworkBase):
    """Law 13: adding villas, partitions, expenses and staff must not add queries per row."""

    def _count(self, url, params):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(url, params).status_code, 200)
        return len(ctx)

    def _grow(self, n, offset):
        from apps.expenses.services import create_expense, get_or_create_category
        for i in range(n):
            villa = create_villa(business=self.business, name=f'Extra {offset + i}', created_by=self.owner)
            staff = User.objects.create_user(f'extra_staff_{offset + i}', password=PASSWORD)
            assign_villa_staff(user=staff, villa=villa, assigned_by=self.owner)
            assign_villa_staff(user=self.staff, villa=villa, assigned_by=self.owner)
            partition = create_partition(villa=villa, name='P', created_by=self.owner)
            create_tenant(partition=partition, name='T', move_in_date=date(2026, 1, 1), monthly_rent=Decimal('100.00'), created_by=self.owner)
            create_expense(villa=villa, category=get_or_create_category(name=f'E{offset + i}'), amount=Decimal('10.00'), date=date(YEAR, MONTH, 1), due_date=date(2026, 1, 5), created_by=self.owner)

    def test_pages_do_not_query_per_row(self):
        pages = [(reverse('villas:villa_list'), self.period), (reverse('expenses:list'), {}), (reverse('villas:villa_detail', args=[self.villa.pk]), self.period)]
        for username in ('owner', 'staff'):
            self.login(username)
            for url, params in pages:
                self._count(url, params)  # warm caches
        self._grow(2, 0)
        few = {}
        for username in ('owner', 'staff'):
            self.login(username)
            few[username] = [self._count(url, params) for url, params in pages]
        self._grow(6, 10)
        for username in ('owner', 'staff'):
            self.login(username)
            self.assertEqual([self._count(url, params) for url, params in pages], few[username], username)
        self.login('owner')
        accountability = reverse('cash:staff_accountability')
        self._count(accountability, self.period)
        before = self._count(accountability, self.period)
        self._grow(4, 30)
        self.assertEqual(self._count(accountability, self.period), before)


class FixedExpenseChecklistTests(FieldworkBase):

    def _rows(self, year, month, user='owner'):
        self.login(user)
        ctx = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), {'period': 'month', 'year': year, 'month': month}).context
        return {r['template'].category.name: r for r in ctx['fixed_rows']}, ctx['fixed_totals']

    def test_every_month_lists_all_fixed_expenses_with_paid_status(self):
        self.set_up_fixed_expenses()
        self.assertFalse(Expense.objects.exists())
        # Opening the month is all it takes: the five fixed expenses are there, the auto-debited rent already paid.
        rows, totals = self._rows(YEAR, MONTH)
        self.assertEqual(Expense.objects.filter(villa=self.villa).count(), 5)
        self.assertEqual(rows['Villa owner rent']['status'], 'paid')
        unpaid = {name for name, r in rows.items() if r['status'] in ('unpaid', 'overdue', 'due_today')}
        self.assertEqual(unpaid, {'Kharama', 'Wifi', 'Maintenance', 'Villa service'})
        self.assertEqual((totals['total'], totals['paid'], totals['unpaid'], totals['unpaid_count']), (Decimal('12350.00'), Decimal('9500.00'), Decimal('2850.00'), 4))
        self._rows(YEAR, MONTH)
        self.assertEqual(Expense.objects.filter(villa=self.villa).count(), 5)  # opening it again adds nothing

        # Staff tap Paid on their three: they now wait for the owner. The owner's own villa service stays unpaid.
        self.login('staff')
        for name in ('Kharama', 'Wifi', 'Maintenance'):
            expense = Expense.objects.get(recurring__category__name=name)
            self.assertEqual(self.client.post(reverse('expenses:quick_paid', args=[expense.pk])).status_code, 302)
        rows, totals = self._rows(YEAR, MONTH, 'staff')
        self.assertEqual({n for n, r in rows.items() if r['status'] == 'to_verify'}, {'Kharama', 'Wifi', 'Maintenance'})
        self.assertEqual(rows['Villa service']['status'] in ('unpaid', 'overdue', 'due_today'), True)
        self.assertFalse(rows['Villa service']['expense'].can_pay)
        self.assertEqual((totals['unpaid'], totals['to_verify_count']), (Decimal('300.00'), 3))

        # The owner verifies one tap at a time and pays the villa service; the card flags clear.
        self.login('owner')
        card = [v for v in self.client.get(reverse('villas:villa_list'), self.period).context['page_obj'] if v.pk == self.villa.pk][0]
        self.assertEqual((card.fixed_pending, card.to_verify), (1, 3))
        for name in ('Kharama', 'Wifi', 'Maintenance'):
            self.client.post(reverse('expenses:verify', args=[Expense.objects.get(recurring__category__name=name).pk]))
        service = Expense.objects.get(recurring__category__name='Villa service')
        self.client.post(reverse('expenses:quick_paid', args=[service.pk]))
        card = [v for v in self.client.get(reverse('villas:villa_list'), self.period).context['page_obj'] if v.pk == self.villa.pk][0]
        self.assertEqual((card.fixed_pending, card.to_verify), (0, 0))
        rows, _ = self._rows(YEAR, MONTH)
        self.assertTrue(all(r['status'] == 'paid' for r in rows.values()))

        # A month still in the future is listed but never created ahead of time.
        next_year, next_month = (YEAR + 1, 1) if MONTH == 12 else (YEAR, MONTH + 1)
        rows, totals = self._rows(next_year, next_month)
        self.assertTrue(all(r['status'] == 'not_created' for r in rows.values()))
        self.assertFalse(Expense.objects.filter(period=date(next_year, next_month, 1)).exists())

    def test_stopped_fixed_expense_still_shows_in_months_it_was_billed(self):
        self.set_up_fixed_expenses()
        self.generate_month('owner')
        wifi = RecurringExpense.objects.get(category__name='Wifi')
        self.client.post(reverse('expenses:recurring_stop', args=[wifi.pk]), {'reason': 'cancelled'})
        rows, _ = self._rows(YEAR, MONTH)
        self.assertIn('Wifi', rows)
        next_year, next_month = (YEAR + 1, 1) if MONTH == 12 else (YEAR, MONTH + 1)
        rows, _ = self._rows(next_year, next_month)
        self.assertNotIn('Wifi', rows)
