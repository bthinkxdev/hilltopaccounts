"""Collect takes everything pending up to the selected month (oldest first, never more than the total), and each payment counts
as income of the month it was collected, or of its own month when that is a coming month."""
from datetime import date
from decimal import Decimal

from django.urls import reverse

from apps.billing.exceptions import NothingToCollect, PaymentExceedsOutstanding
from apps.billing.models import Invoice, Payment
from apps.billing.services import collect_rent
from apps.shared.income import income_month_for
from apps.tenancy.services import collect_deposit, update_tenant
from .tests_villa_stats import LAST_MONTH, NEXT_MONTH, RENT, THIS_MONTH, VillaStatsBase


class IncomeMonthRuleTests(VillaStatsBase):
    MOVE_IN = LAST_MONTH  # an earlier unpaid month exists

    def test_the_later_of_the_rent_month_and_the_collected_month(self):
        self.assertEqual(income_month_for(date(2026, 10, 1), date(2026, 11, 16)), date(2026, 11, 1))  # old rent collected now
        self.assertEqual(income_month_for(date(2026, 11, 1), date(2026, 11, 16)), date(2026, 11, 1))
        self.assertEqual(income_month_for(date(2026, 12, 1), date(2026, 11, 16)), date(2026, 12, 1))  # paid ahead
        self.assertEqual(income_month_for(date(2026, 10, 1), date(2026, 10, 5)), date(2026, 10, 1))  # back-dated


class CollectUpToTheSelectedMonthTests(VillaStatsBase):
    MOVE_IN = LAST_MONTH  # an earlier unpaid month exists

    def collect(self, partition=None, month=THIS_MONTH, amount=None, **kw):
        return collect_rent(partition=partition or self.p2, year=month.year, month=month.month, method='cash', collected_by=self.owner, amount=amount, **kw)

    def months_paid(self, partition=None):
        return {i.billing_period_start: sum((p.amount for p in i.payments.filter(is_cancelled=False)), Decimal('0.00')) for i in Invoice.objects.filter(partition=partition or self.p2, is_cancelled=False)}

    def test_a_blank_amount_collects_everything_pending_up_to_the_month(self):
        payments = self.collect()
        self.assertEqual(len(payments), 2)  # last month's and this month's rent
        self.assertEqual(self.months_paid(), {LAST_MONTH: RENT, THIS_MONTH: RENT})

    def test_a_smaller_amount_pays_the_oldest_month_first(self):
        self.collect(amount=Decimal('3000.00'))
        self.assertEqual(self.months_paid(), {LAST_MONTH: RENT, THIS_MONTH: Decimal('700.00')})
        self.collect(amount=Decimal('1600.00'))  # the rest
        self.assertEqual(self.months_paid(), {LAST_MONTH: RENT, THIS_MONTH: RENT})

    def test_a_small_amount_stays_inside_the_oldest_month(self):
        self.collect(amount=Decimal('1000.00'))
        self.assertEqual(self.months_paid(), {LAST_MONTH: Decimal('1000.00'), THIS_MONTH: Decimal('0.00')})

    def test_more_than_the_total_is_refused_and_nothing_is_recorded(self):
        with self.assertRaises(PaymentExceedsOutstanding):
            self.collect(amount=Decimal('4600.01'))
        self.assertFalse(Payment.objects.filter(invoice__partition=self.p2).exists())

    def test_collecting_for_an_earlier_month_takes_only_up_to_that_month(self):
        self.collect(month=LAST_MONTH)
        self.assertEqual(self.months_paid().get(THIS_MONTH, Decimal('0.00')), Decimal('0.00'))  # this month is not part of "up to last month"
        with self.assertRaises(NothingToCollect):
            self.collect(month=LAST_MONTH)  # nothing more is pending up to last month

    def test_the_popup_total_is_the_earlier_months_plus_this_month(self):
        self.login('owner')
        ctx = self.page(THIS_MONTH)
        row = next(r for r in ctx['rent_rows'] if r['partition'].pk == self.p2.pk)
        self.assertEqual((row['balance'], row['prior_unpaid_total'], row['pending_through']), (RENT, RENT, RENT * 2))
        self.collect(amount=Decimal('1000.00'))
        row = next(r for r in self.page(THIS_MONTH)['rent_rows'] if r['partition'].pk == self.p2.pk)
        self.assertEqual(row['pending_through'], RENT * 2 - Decimal('1000.00'))

    def test_the_collect_view_defaults_to_the_total_and_refuses_more(self):
        self.login('owner')
        url = reverse('billing:rent_collect', args=[self.p2.pk])
        data = {'year': THIS_MONTH.year, 'month': THIS_MONTH.month, 'method': 'cash', 'next': '/'}
        self.client.post(url, {**data, 'amount': '9999.00'})
        self.assertFalse(Payment.objects.filter(invoice__partition=self.p2).exists())
        self.client.post(url, data)  # blank: everything
        self.assertEqual(self.months_paid(), {LAST_MONTH: RENT, THIS_MONTH: RENT})


    def test_a_charge_added_to_an_earlier_month_is_collectable_with_the_popup_total(self):
        """The popup lists the charge of an earlier month as pending; Confirm with that total must be accepted."""
        from apps.billing.selectors import rent_sheet
        from apps.billing.services import create_charge, get_or_create_charge_type
        from apps.billing.models import Charge
        create_charge(partition=self.p2, charge_type=get_or_create_charge_type(name='Gas'), amount=Decimal('100.00'), start_date=LAST_MONTH, created_by=self.owner, frequency=Charge.Frequency.MONTHLY)
        self.login('owner')
        row = next(r for r in self.page(THIS_MONTH)['rent_rows'] if r['partition'].pk == self.p2.pk)
        shown = row['pending_through']
        self.assertEqual(shown, (RENT + Decimal('100.00')) * 2)
        payments = self.collect(amount=shown)
        self.assertEqual(sum(p.amount for p in payments), shown)
        row = next(r for r in self.page(THIS_MONTH)['rent_rows'] if r['partition'].pk == self.p2.pk)
        self.assertEqual(row['pending_through'], Decimal('0.00'))


class CollectedIncomeMonthTests(VillaStatsBase):
    MOVE_IN = LAST_MONTH  # an earlier unpaid month exists

    def income(self, month):
        self.login('owner')
        return self.page(month)['pnl']['income']

    def collect(self, month=THIS_MONTH, amount=None, **kw):
        return collect_rent(partition=self.p2, year=month.year, month=month.month, method='cash', collected_by=self.owner, amount=amount, **kw)

    def test_old_rent_collected_now_is_this_months_income(self):
        payments = self.collect()
        self.assertEqual({p.invoice.billing_period_start: p.income_month for p in payments}, {LAST_MONTH: THIS_MONTH, THIS_MONTH: THIS_MONTH})
        self.assertEqual(self.income(THIS_MONTH), RENT * 2)
        self.assertEqual(self.income(LAST_MONTH), Decimal('0.00'))

    def test_rent_collected_ahead_is_that_future_months_income(self):
        payments = self.collect(month=NEXT_MONTH)
        by_month = {p.invoice.billing_period_start: p.income_month for p in payments}
        self.assertEqual(by_month, {LAST_MONTH: THIS_MONTH, THIS_MONTH: THIS_MONTH, NEXT_MONTH: NEXT_MONTH})
        self.assertEqual(self.income(THIS_MONTH), RENT * 2)
        self.assertEqual(self.income(NEXT_MONTH), RENT)

    def test_a_back_dated_collection_counts_in_its_own_month(self):
        payments = self.collect(month=LAST_MONTH, collected_at=LAST_MONTH.replace(day=5))
        self.assertEqual(payments[0].income_month, LAST_MONTH)
        self.assertEqual(self.income(LAST_MONTH), RENT)

    def test_the_total_bar_is_this_months_income(self):
        self.collect()
        self.login('owner')
        r = self.client.get(reverse('billing:payment_list'), {'year': THIS_MONTH.year, 'month': THIS_MONTH.month, 'period': 'month'})
        bar = r.context['totals_bar']
        self.assertEqual(bar['collected'], RENT * 2)
        self.assertEqual(bar['collected'], self.page(THIS_MONTH)['pnl']['income'])

    def test_an_advance_follows_the_same_rule(self):
        update_tenant(tenant=self.p2.current_tenant, updated_by=self.owner, deposit=3000)
        collect_deposit(tenant=self.p2.current_tenant, amount=Decimal('1000.00'), method='cash', collected_by=self.owner)
        self.assertEqual(self.p2.current_tenant.deposit_collections.get().income_month, THIS_MONTH)  # moved in last month, collected now
        future = self.occupied('F1', move_in=NEXT_MONTH)
        update_tenant(tenant=future.current_tenant, updated_by=self.owner, deposit=2000)
        collect_deposit(tenant=future.current_tenant, amount=Decimal('2000.00'), method='cash', collected_by=self.owner)
        self.assertEqual(future.current_tenant.deposit_collections.get().income_month, NEXT_MONTH)  # paid ahead of the move-in month


class OtherPagesFollowTheIncomeMonthTests(VillaStatsBase):
    """The staff list and the cash handover list use the same month as Collections and the Income card."""
    MOVE_IN = LAST_MONTH

    def test_the_staff_list_counts_what_was_collected_in_the_month(self):
        collect_rent(partition=self.p1, year=LAST_MONTH.year, month=LAST_MONTH.month, method='cash', collected_by=self.staff)  # last month's rent, collected today
        self.login('owner')
        collected = lambda m: {u.username: u.collected_in_period for u in self.client.get(reverse('accounts:staff_list'), {'period': 'month', 'year': m.year, 'month': m.month}).context['page_obj']}
        self.assertEqual(collected(THIS_MONTH)['staff'], RENT)  # counts in the month it was collected
        self.assertEqual(collected(LAST_MONTH)['staff'], 0)

    def test_a_handover_is_listed_under_the_month_its_cash_counts_in(self):
        from apps.cash.models import CashHandover
        from apps.cash.services import submit_cash_handover
        [payment] = collect_rent(partition=self.p1, year=LAST_MONTH.year, month=LAST_MONTH.month, method='cash', collected_by=self.staff)
        handover = submit_cash_handover(staff=self.staff, payments=[payment], submitted_by=self.staff)
        self.login('owner')
        listed = lambda m: [h.pk for h in self.client.get(reverse('cash:handover_list'), {'period': 'month', 'year': m.year, 'month': m.month}).context['page_obj']]
        self.assertIn(handover.pk, listed(THIS_MONTH))
        self.assertNotIn(handover.pk, listed(LAST_MONTH))

    def test_the_collect_message_names_every_month_it_paid(self):
        self.login('owner')
        r = self.client.post(reverse('billing:rent_collect', args=[self.p1.pk]), {'year': THIS_MONTH.year, 'month': THIS_MONTH.month, 'method': 'cash', 'next': '/'}, follow=True)
        text = ' '.join(str(m) for m in r.context['messages'])
        self.assertIn('collected for', text)
        self.assertIn(f'{LAST_MONTH:%B %Y} QAR {RENT}', text)
        self.assertIn(f'{THIS_MONTH:%B %Y} QAR {RENT}', text)
