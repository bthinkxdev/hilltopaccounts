"""Reverting a collection or a payment marked by mistake.

What must hold, every time: only that one record changes (never another month or another payment); everything it fed
(pending amounts, income, cash, alerts) goes back by exactly that amount; the reason is mandatory; the action is
permission-checked, guarded against handovers and double clicks, and audited."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import Action, AuditLog
from apps.billing.models import Invoice, Payment
from apps.billing.services import cancel_payment
from apps.cash.models import CashHandover
from apps.cash.services import reject_cash_handover, submit_cash_handover
from apps.expenses.models import Expense, PaidBy
from apps.expenses.services import (ExpenseNotPayable, create_expense, create_recurring_expense, get_or_create_category,
                                    mark_expense_paid, revert_expense_payment, settle_auto_debits, verify_expense)
from apps.shared.exceptions import DomainError
from .tests_villa_stats import LAST_MONTH, THIS_MONTH, VillaStatsBase

MODAL = {'HTTP_X_MODAL': '1'}
TODAY = date.today()


def months_back(n):
    month = THIS_MONTH
    for _ in range(n):
        month = (month - timedelta(days=1)).replace(day=1)
    return month


class ExpensePaymentRevertTests(VillaStatsBase):

    def setUp(self):
        super().setUp()
        self.login('owner')
        self.start = months_back(2)
        self.rec = create_recurring_expense(villa=self.villa, category=get_or_create_category(name='Wi-Fi'), amount=300, due_day=1, paid_by='staff', created_by=self.owner, start_period=self.start)
        self.rows = {e.period: e for e in Expense.objects.filter(recurring=self.rec)}
        self.this = self.rows[THIS_MONTH]

    def reload(self, e):
        return Expense.objects.get(pk=e.pk)

    def snapshot(self, e):
        e = self.reload(e)
        return (e.amount, e.date, e.period, e.status, e.paid_on, e.payment_method, e.paid_by, e.paid_by_user_id, e.verified_at, e.category_id, e.due_date)

    def pay_as_admin(self, e):
        return mark_expense_paid(expense=e, paid_on=TODAY, payment_method='bank_transfer', marked_by=self.owner)

    def test_the_three_months_were_recorded_from_the_start_month(self):
        self.assertEqual(sorted(self.rows), [months_back(2), months_back(1), THIS_MONTH])

    def test_revert_restores_exactly_the_unpaid_state_on_the_side_it_was_on(self):
        before = self.snapshot(self.this)
        self.pay_as_admin(self.this)
        paid = self.reload(self.this)
        self.assertEqual((paid.paid_by, paid.verified_at is not None), (PaidBy.OWNER, True))  # paying flipped it to the admin side
        revert_expense_payment(expense=self.this, reverted_by=self.owner, reason='clicked by mistake')
        self.assertEqual(self.snapshot(self.this), before)  # identical to before it was paid: amount, month, side, payer, method, verification

    def test_revert_touches_nothing_else_not_the_other_months_not_other_expenses(self):
        other = create_expense(villa=self.villa, category=get_or_create_category(name='Lock'), amount=80, date=THIS_MONTH, created_by=self.owner)
        for e in self.rows.values():
            self.pay_as_admin(e)
        mark_expense_paid(expense=other, paid_on=TODAY, payment_method='cash', marked_by=self.owner)
        before = {e.pk: self.snapshot(e) for e in list(self.rows.values()) + [other]}
        revert_expense_payment(expense=self.rows[months_back(1)], reverted_by=self.owner, reason='wrong month')
        after = {pk: self.snapshot(Expense(pk=pk)) for pk in before}
        changed = [pk for pk in before if before[pk] != after[pk]]
        self.assertEqual(changed, [self.rows[months_back(1)].pk])  # exactly one record changed
        self.assertIsNone(self.reload(self.rows[months_back(1)]).paid_on)
        self.assertIsNotNone(self.reload(self.rows[THIS_MONTH]).paid_on)
        self.assertIsNotNone(self.reload(self.rows[months_back(2)]).paid_on)

    def test_profit_does_not_change_because_profit_counts_the_expense_not_its_payment(self):
        before = self.page(THIS_MONTH)['pnl']['expenses']
        self.pay_as_admin(self.this)
        self.assertEqual(self.page(THIS_MONTH)['pnl']['expenses'], before)
        revert_expense_payment(expense=self.this, reverted_by=self.owner, reason='mistake')
        self.assertEqual(self.page(THIS_MONTH)['pnl']['expenses'], before)

    def test_it_can_be_paid_again_after_a_revert(self):
        self.pay_as_admin(self.this)
        revert_expense_payment(expense=self.this, reverted_by=self.owner, reason='mistake')
        self.pay_as_admin(self.reload(self.this))
        self.assertIsNotNone(self.reload(self.this).paid_on)

    def test_a_reason_is_mandatory_and_nothing_changes_without_one(self):
        self.pay_as_admin(self.this)
        paid = self.snapshot(self.this)
        for bad in ('', '   ', None):
            with self.assertRaises(DomainError):
                revert_expense_payment(expense=self.this, reverted_by=self.owner, reason=bad)
        self.assertEqual(self.snapshot(self.this), paid)

    def test_reverting_twice_or_reverting_an_unpaid_expense_is_refused(self):
        with self.assertRaises(ExpenseNotPayable):
            revert_expense_payment(expense=self.this, reverted_by=self.owner, reason='x')
        self.pay_as_admin(self.this)
        revert_expense_payment(expense=self.this, reverted_by=self.owner, reason='x')
        with self.assertRaises(ExpenseNotPayable):
            revert_expense_payment(expense=self.this, reverted_by=self.owner, reason='x again')
        self.assertEqual(AuditLog.objects.filter(action=Action.EXPENSE_PAYMENT_REVERTED, object_id=str(self.this.pk)).count(), 1)

    def test_it_is_audited_with_old_and_new_state_and_the_reason(self):
        self.pay_as_admin(self.this)
        revert_expense_payment(expense=self.this, reverted_by=self.owner, reason='clicked by mistake')
        entry = AuditLog.objects.get(action=Action.EXPENSE_PAYMENT_REVERTED, object_id=str(self.this.pk))
        self.assertEqual((entry.user, entry.reason, entry.villa), (self.owner, 'clicked by mistake', self.villa))
        self.assertNotEqual(entry.old_value['paid_on'], 'None')
        self.assertEqual((entry.new_value['paid_on'], entry.new_value['paid_by']), ('None', PaidBy.STAFF))

    def test_only_the_admin_can_revert_not_staff_not_a_manager_not_an_outsider(self):
        mark_expense_paid(expense=self.this, paid_on=TODAY, payment_method='cash', marked_by=self.staff)  # staff paid it, unverified
        outsider = User.objects.create_user('outsider', password='x-Pass-1234-y')
        for who in (self.staff, self.manager, outsider):
            with self.assertRaises(PermissionDenied):
                revert_expense_payment(expense=self.this, reverted_by=who, reason='x')
        self.assertIsNotNone(self.reload(self.this).paid_on)
        revert_expense_payment(expense=self.this, reverted_by=self.owner, reason='the admin can')
        self.assertIsNone(self.reload(self.this).paid_on)

    def test_a_payment_inside_a_handover_cannot_be_reverted_until_the_handover_is_rejected(self):
        self.login('staff')
        self.collect(self.p1, THIS_MONTH)
        rent = list(Payment.objects.filter(collected_by=self.staff, method='cash'))
        mark_expense_paid(expense=self.this, paid_on=TODAY, payment_method='cash', marked_by=self.staff)
        handover = submit_cash_handover(staff=self.staff, payments=rent, expenses=[self.reload(self.this)], submitted_by=self.staff)
        with self.assertRaises(DomainError):
            revert_expense_payment(expense=self.this, reverted_by=self.owner, reason='x')
        self.assertIsNotNone(self.reload(self.this).paid_on)
        reject_cash_handover(handover=handover, rejected_by=self.owner, reason='recount')
        revert_expense_payment(expense=self.this, reverted_by=self.owner, reason='x')
        self.assertIsNone(self.reload(self.this).paid_on)

    def test_an_auto_debit_that_is_already_due_cannot_be_reverted_because_it_would_just_be_paid_again(self):
        auto = create_recurring_expense(villa=self.villa, category=get_or_create_category(name='Owner debit'), amount=900, due_day=1, created_by=self.owner, auto_debit=True, start_period=THIS_MONTH)
        settle_auto_debits(today=TODAY)
        e = Expense.objects.get(recurring=auto, period=THIS_MONTH)
        self.assertIsNotNone(e.paid_on)
        with self.assertRaises(DomainError) as ctx:
            revert_expense_payment(expense=e, reverted_by=self.owner, reason='x')
        self.assertIn('auto-debit', str(ctx.exception).lower())
        self.assertIsNotNone(self.reload(e).paid_on)

    def test_a_sold_villa_is_refused(self):
        self.pay_as_admin(self.this)
        self.villa.is_sold = True
        self.villa.save(update_fields=['is_sold'])
        with self.assertRaises(DomainError):
            revert_expense_payment(expense=self.this, reverted_by=self.owner, reason='x')

    def test_cancelled_expenses_have_no_payment_to_revert(self):
        self.pay_as_admin(self.this)
        Expense.objects.filter(pk=self.this.pk).update(status=Expense.Status.CANCELLED)
        with self.assertRaises(ExpenseNotPayable):
            revert_expense_payment(expense=self.this, reverted_by=self.owner, reason='x')

    def test_the_side_is_restored_when_the_other_side_paid_it(self):
        # an admin-side monthly expense that staff paid goes back to the admin side
        rec = create_recurring_expense(villa=self.villa, category=get_or_create_category(name='Paint'), amount=500, due_day=1, paid_by='owner', created_by=self.owner, start_period=THIS_MONTH)
        e = Expense.objects.get(recurring=rec, period=THIS_MONTH)
        mark_expense_paid(expense=e, paid_on=TODAY, payment_method='cash', marked_by=self.staff)
        self.assertEqual(self.reload(e).paid_by, PaidBy.STAFF)
        revert_expense_payment(expense=e, reverted_by=self.owner, reason='x')
        self.assertEqual(self.reload(e).paid_by, PaidBy.OWNER)

    def test_a_one_time_expense_recorded_already_paid_goes_back_to_the_side_of_whoever_recorded_it(self):
        e = create_expense(villa=self.villa, category=get_or_create_category(name='Gas'), amount=60, date=THIS_MONTH, created_by=self.owner, paid_on=TODAY, payment_method='cash')
        revert_expense_payment(expense=e, reverted_by=self.owner, reason='recorded as paid by mistake')
        fresh = self.reload(e)
        self.assertEqual((fresh.paid_on, fresh.paid_by, fresh.amount), (None, PaidBy.OWNER, 60))

    def test_a_month_paid_ahead_keeps_its_record_and_amount_but_is_unpaid_again(self):
        future = (THIS_MONTH + timedelta(days=35)).replace(day=1)
        from apps.expenses.services import pay_fixed_expense_ahead
        paid = pay_fixed_expense_ahead(recurring=self.rec, year=future.year, month=future.month, paid_on=TODAY, payment_method='cash', marked_by=self.owner, amount=410)
        revert_expense_payment(expense=paid, reverted_by=self.owner, reason='paid the wrong month')
        fresh = self.reload(paid)
        self.assertEqual((fresh.paid_on, fresh.amount, fresh.period, fresh.paid_by), (None, 410, future, PaidBy.STAFF))
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.amount, 300)  # the schedule is untouched

    # ---- the popup -------------------------------------------------------------------------------------------------

    def url(self, e, next_url=None):
        url = reverse('expenses:revert_payment', args=[e.pk])
        return f'{url}?next={next_url}' if next_url else url

    def test_the_button_shows_only_where_it_applies(self):
        r = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]))
        self.assertNotContains(r, reverse('expenses:revert_payment', args=[self.this.pk]))  # unpaid: nothing to revert
        self.pay_as_admin(self.this)
        r = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]))
        self.assertContains(r, reverse('expenses:revert_payment', args=[self.this.pk]))
        self.assertContains(self.client.get(reverse('expenses:list')), reverse('expenses:revert_payment', args=[self.this.pk]))
        for username in ('manager', 'staff'):  # nobody but the Admin gets the button
            self.login(username)
            self.assertNotContains(self.client.get(reverse('villas:villa_detail', args=[self.villa.pk])), reverse('expenses:revert_payment', args=[self.this.pk]), msg_prefix=username)
            self.assertNotContains(self.client.get(reverse('expenses:list')), reverse('expenses:revert_payment', args=[self.this.pk]), msg_prefix=username)

    def test_the_popup_needs_a_reason_then_reverts_and_goes_back_to_the_page_it_came_from(self):
        self.pay_as_admin(self.this)
        r = self.client.get(self.url(self.this), **MODAL)
        self.assertContains(r, 'data-modal-fragment')
        self.assertContains(r, 'Revert payment')
        self.assertContains(r, 'data-modal-cancel')
        r = self.client.post(self.url(self.this), {'reason': ''}, **MODAL)  # no reason
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('X-Modal-Redirect', r)
        self.assertIsNotNone(self.reload(self.this).paid_on)
        back = reverse('expenses:list')
        r = self.client.post(self.url(self.this, back), {'reason': 'I tapped Paid by accident'}, **MODAL)
        self.assertEqual(r['X-Modal-Redirect'], back)
        self.assertIsNone(self.reload(self.this).paid_on)
        self.assertContains(self.client.get(back), 'reverted')  # the success message is waiting on the next page

    def test_an_outside_address_in_next_is_ignored(self):
        self.pay_as_admin(self.this)
        r = self.client.post(self.url(self.this, 'https://evil.example/steal'), {'reason': 'x'}, **MODAL)
        self.assertNotIn('evil.example', r['X-Modal-Redirect'])

    def test_someone_who_may_not_revert_gets_a_403_and_nothing_changes(self):
        self.pay_as_admin(self.this)
        self.login('staff')  # an admin-paid expense: not theirs
        self.assertEqual(self.client.post(self.url(self.this), {'reason': 'x'}).status_code, 403)
        self.assertIsNotNone(self.reload(self.this).paid_on)


class RentCollectionRevertTests(VillaStatsBase):

    def setUp(self):
        super().setUp()
        self.login('owner')

    def payments(self, partition, month):
        return list(Payment.objects.filter(invoice__partition=partition, invoice__billing_period_start=month).order_by('pk'))

    def invoice(self, partition, month):
        return Invoice.objects.get(partition=partition, billing_period_start=month)

    def outstanding(self, partition, month):
        from apps.billing import selectors as bs
        return bs.invoice_outstanding(self.invoice(partition, month))

    def pending_total(self):
        return self.client.get(reverse('villas:villa_detail', args=[self.villa.pk])).context['pending']['rent_total']

    def test_reverting_puts_back_exactly_that_amount_and_touches_no_other_month(self):
        self.collect(self.p1, LAST_MONTH)
        self.collect(self.p1, THIS_MONTH)
        self.collect(self.p2, THIS_MONTH)
        last, this = self.payments(self.p1, LAST_MONTH)[0], self.payments(self.p1, THIS_MONTH)[0]
        before = {'last_out': self.outstanding(self.p1, LAST_MONTH), 'this_out': self.outstanding(self.p1, THIS_MONTH), 'p2_this': self.outstanding(self.p2, THIS_MONTH), 'pending': self.pending_total(), 'income_last': self.page(LAST_MONTH)['pnl']['income'], 'income_this': self.page(THIS_MONTH)['pnl']['income']}
        cancel_payment(payment=last, cancelled_by=self.owner, reason='clicked by mistake')
        last.refresh_from_db()
        self.assertTrue(last.is_cancelled)
        self.assertEqual(last.cancelled_reason, 'clicked by mistake')
        self.assertEqual(self.outstanding(self.p1, LAST_MONTH), before['last_out'] + last.amount)  # exactly that amount is pending again
        self.assertEqual(self.outstanding(self.p1, THIS_MONTH), before['this_out'])  # the next month is untouched
        self.assertEqual(self.outstanding(self.p2, THIS_MONTH), before['p2_this'])  # another room is untouched
        self.assertEqual(self.pending_total(), before['pending'] + last.amount)
        self.assertEqual(self.page(LAST_MONTH)['pnl']['income'], before['income_last'] - last.amount)  # last month's income falls
        self.assertEqual(self.page(THIS_MONTH)['pnl']['income'], before['income_this'])
        self.assertFalse(Payment.objects.get(pk=this.pk).is_cancelled)

    def test_a_reason_is_mandatory(self):
        self.collect(self.p1, THIS_MONTH)
        payment = self.payments(self.p1, THIS_MONTH)[0]
        for bad in ('', '  ', None):
            with self.assertRaises(DomainError):
                cancel_payment(payment=payment, cancelled_by=self.owner, reason=bad)
        self.assertFalse(Payment.objects.get(pk=payment.pk).is_cancelled)

    def test_a_double_click_cannot_revert_twice(self):
        self.collect(self.p1, THIS_MONTH)
        payment = self.payments(self.p1, THIS_MONTH)[0]
        cancel_payment(payment=Payment.objects.get(pk=payment.pk), cancelled_by=self.owner, reason='x')
        with self.assertRaises(DomainError):
            cancel_payment(payment=Payment.objects.get(pk=payment.pk), cancelled_by=self.owner, reason='x again')
        self.assertEqual(AuditLog.objects.filter(action=Action.COLLECTION_CANCELLED, object_id=str(payment.pk)).count(), 1)

    def test_it_is_audited_and_the_collected_alert_is_closed(self):
        from apps.notifications.models import Notification
        self.login('staff')
        self.collect(self.p1, THIS_MONTH)
        payment = self.payments(self.p1, THIS_MONTH)[0]
        alert = Notification.objects.filter(dedupe_key=f'collection_recorded:{payment.pk}')
        self.assertTrue(alert.exists())
        cancel_payment(payment=payment, cancelled_by=self.owner, reason='duplicate')
        self.assertFalse(alert.filter(resolved_at__isnull=True).exists())
        entry = AuditLog.objects.get(action=Action.COLLECTION_CANCELLED, object_id=str(payment.pk))
        self.assertEqual((entry.user, entry.reason, entry.villa), (self.owner, 'duplicate', self.villa))

    def test_only_the_admin_can_revert_a_collection_not_staff_not_a_manager_not_an_outsider(self):
        self.login('staff')
        self.collect(self.p2, THIS_MONTH)  # staff's own collection
        own = self.payments(self.p2, THIS_MONTH)[0]
        outsider = User.objects.create_user('outsider2', password='x-Pass-1234-y')
        for who in (self.staff, self.manager, outsider):
            with self.assertRaises(PermissionDenied):
                cancel_payment(payment=own, cancelled_by=who, reason='x')
        self.assertFalse(Payment.objects.get(pk=own.pk).is_cancelled)
        cancel_payment(payment=own, cancelled_by=self.owner, reason='the admin can')
        self.assertTrue(Payment.objects.get(pk=own.pk).is_cancelled)

    def test_cash_inside_a_handover_cannot_be_reverted_until_it_is_rejected(self):
        self.login('staff')
        self.collect(self.p1, THIS_MONTH)
        payment = self.payments(self.p1, THIS_MONTH)[0]
        handover = submit_cash_handover(staff=self.staff, payments=[payment], submitted_by=self.staff)
        with self.assertRaises(DomainError):
            cancel_payment(payment=payment, cancelled_by=self.owner, reason='x')
        reject_cash_handover(handover=handover, rejected_by=self.owner, reason='recount')
        cancel_payment(payment=Payment.objects.get(pk=payment.pk), cancelled_by=self.owner, reason='x')

    def test_a_sold_villa_is_refused(self):
        self.collect(self.p1, THIS_MONTH)
        payment = self.payments(self.p1, THIS_MONTH)[0]
        self.villa.is_sold = True
        self.villa.save(update_fields=['is_sold'])
        with self.assertRaises(DomainError):
            cancel_payment(payment=payment, cancelled_by=self.owner, reason='x')

    def test_after_a_revert_the_rent_can_be_collected_again_through_the_normal_rules(self):
        self.collect(self.p1, THIS_MONTH)
        payment = self.payments(self.p1, THIS_MONTH)[0]
        cancel_payment(payment=payment, cancelled_by=self.owner, reason='x')
        self.assertEqual(self.outstanding(self.p1, THIS_MONTH), payment.amount)
        self.collect(self.p1, THIS_MONTH)
        self.assertEqual(self.outstanding(self.p1, THIS_MONTH), 0)

    # ---- the popup and where the Revert buttons are ----------------------------------------------------------------

    def test_the_popup_flow_and_the_return_to_the_page_it_came_from(self):
        self.collect(self.p1, LAST_MONTH)
        payment = self.payments(self.p1, LAST_MONTH)[0]
        url = reverse('billing:payment_cancel', args=[payment.pk])
        r = self.client.get(url, **MODAL)
        self.assertContains(r, 'Revert collection')
        self.assertContains(r, 'data-modal-cancel')
        r = self.client.post(url, {'reason': ''}, **MODAL)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Payment.objects.get(pk=payment.pk).is_cancelled)
        back = reverse('billing:payment_list')
        r = self.client.post(f'{url}?next={back}', {'reason': 'accidental click'}, **MODAL)
        self.assertEqual(r['X-Modal-Redirect'], back)
        self.assertTrue(Payment.objects.get(pk=payment.pk).is_cancelled)
        self.assertContains(self.client.get(back), 'reverted')
        # the link is gone once reverted, and a second visit is harmless
        r = self.client.get(url, **MODAL)
        self.assertIn('X-Modal-Redirect', r)  # already reverted: sent back with a message, no second revert possible

    def test_the_revert_button_is_on_the_villa_page_the_collections_page_and_the_partition_page(self):
        self.collect(self.p1, THIS_MONTH)
        payment = self.payments(self.p1, THIS_MONTH)[0]
        link = reverse('billing:payment_cancel', args=[payment.pk])
        self.assertContains(self.client.get(reverse('villas:villa_detail', args=[self.villa.pk])), link)
        self.assertContains(self.client.get(reverse('billing:payment_list'), {'tab': 'collected', 'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month}), link)
        self.assertContains(self.client.get(reverse('villas:partition_detail', args=[self.p1.pk])), link)
        for username in ('manager', 'staff'):  # nobody but the Admin gets the button
            self.login(username)
            self.assertNotContains(self.client.get(reverse('villas:villa_detail', args=[self.villa.pk])), link, msg_prefix=username)
            self.assertNotContains(self.client.get(reverse('billing:payment_list'), {'tab': 'collected', 'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month}), link, msg_prefix=username)

    def test_a_reverted_collection_leaves_the_collected_list_and_the_pending_list_shows_the_rent_again(self):
        self.collect(self.p1, THIS_MONTH)
        payment = self.payments(self.p1, THIS_MONTH)[0]
        cancel_payment(payment=payment, cancelled_by=self.owner, reason='x')
        r = self.client.get(reverse('billing:payment_list'), {'tab': 'collected', 'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month})
        self.assertNotContains(r, reverse('billing:payment_cancel', args=[payment.pk]))
        self.assertTrue(any(i['partition'].pk == self.p1.pk and i['month'] == THIS_MONTH.month for i in self.client.get(reverse('villas:villa_detail', args=[self.villa.pk])).context['pending']['rent']))


class AdvanceCollectionRevertTests(VillaStatsBase):

    def setUp(self):
        super().setUp()
        from apps.tenancy.services import collect_deposit, update_tenant
        self.login('owner')
        self.tenant = self.p1.current_tenant
        update_tenant(tenant=self.tenant, updated_by=self.owner, deposit=3000)
        self.collection = collect_deposit(tenant=self.tenant, amount=1000, method='cash', collected_by=self.owner)

    def test_the_advance_collection_revert_is_on_the_collections_page_and_follows_the_same_rule(self):
        link = reverse('tenancy:deposit_void', args=[self.collection.pk])
        month = self.tenant.move_in_date
        r = self.client.get(reverse('billing:payment_list'), {'tab': 'collected', 'period': 'month', 'year': month.year, 'month': month.month})
        self.assertContains(r, link)
        for username in ('staff', 'manager'):  # only the Admin may revert
            self.login(username)
            self.assertEqual(self.client.post(link, {'reason': 'x'}).status_code, 403, username)
        self.login('owner')
        resp = self.client.post(f'{link}?next={reverse("billing:payment_list")}', {'reason': 'accidental'}, **MODAL)
        self.assertEqual(resp['X-Modal-Redirect'], reverse('billing:payment_list'))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.deposit_paid, 0)
        self.assertEqual(self.tenant.deposit_pending, 3000)
