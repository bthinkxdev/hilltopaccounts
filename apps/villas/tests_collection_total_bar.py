"""The Collections total bar: every figure is the sum of exactly the rows the current tab and filters show, checked against an
independent calculation straight from the database."""
from decimal import Decimal

from django.db.models import Sum
from django.urls import reverse

from apps.billing import selectors as bs
from apps.billing.models import Invoice, Payment
from apps.billing.services import cancel_payment
from apps.tenancy.models import DepositCollection
from apps.tenancy.services import collect_deposit, create_tenant, update_tenant
from apps.villas.services import create_partition, create_villa
from .tests_villa_stats import LAST_MONTH, RENT, THIS_MONTH, VillaStatsBase

ZERO = Decimal('0.00')


class TotalBarBase(VillaStatsBase):

    def setUp(self):
        super().setUp()
        self.login('owner')
        self.villa_b = create_villa(business=self.business, name='Villa B', created_by=self.owner)
        self.pb = create_partition(villa=self.villa_b, name='B1', created_by=self.owner)
        create_tenant(partition=self.pb, name='T B1', move_in_date=LAST_MONTH, monthly_rent=RENT, created_by=self.owner)
        self.collect(self.p1, THIS_MONTH)                                    # admin, full
        self.login('staff')
        self.collect(self.p2, THIS_MONTH, amount=1000)                       # staff, partial
        self.login('owner')
        self.client.post(reverse('billing:rent_collect', args=[self.pb.pk]), {'year': THIS_MONTH.year, 'month': THIS_MONTH.month, 'method': 'bank_transfer', 'amount': '700', 'next': '/'})
        self.collect(self.p2, LAST_MONTH)                                    # belongs to last month
        tenant = self.p1.current_tenant
        update_tenant(tenant=tenant, updated_by=self.owner, deposit=3000)
        collect_deposit(tenant=tenant, amount=1200, method='cash', collected_by=self.staff)  # advance: belongs to the move-in month

    def bar(self, tab='all', **params):
        r = self.client.get(reverse('billing:payment_list'), {'tab': tab, **params})
        self.assertEqual(r.status_code, 200)
        return r, r.context['totals_bar']

    def month(self, month=THIS_MONTH, **extra):
        return {'period': 'month', 'year': month.year, 'month': month.month, **extra}

    def paid_for(self, month, **flt):
        return Payment.objects.filter(is_cancelled=False, invoice__billing_period_start=month, **flt).aggregate(t=Sum('amount'))['t'] or ZERO


class CollectedTabBarTests(TotalBarBase):

    def test_the_total_is_what_was_collected_for_the_month_and_equals_its_rows(self):
        r, b = self.bar('collected', **self.month())
        self.assertEqual(b['kind'], 'collected')
        self.assertEqual(b['total'], self.paid_for(THIS_MONTH))
        self.assertEqual(b['total'], sum((x['amount'] for x in r.context['received_payments']), ZERO))
        self.assertEqual(b['count'], len(r.context['received_payments']))
        self.assertEqual((b['rent'], b['advance']), (b['total'], ZERO))  # the advance belongs to its move-in month
        self.assertContains(r, 'data-total-bar="collected"')
        self.assertContains(r, f'QAR {b["total"]:,.2f}')

    def test_an_advance_counts_in_its_own_month(self):
        _, b = self.bar('collected', **self.month(LAST_MONTH))
        self.assertEqual((b['rent'], b['advance'], b['total']), (self.paid_for(LAST_MONTH), Decimal('1200.00'), self.paid_for(LAST_MONTH) + Decimal('1200.00')))

    def test_each_filter_gives_exactly_the_matching_rows(self):
        m = self.month()
        self.assertEqual(self.bar('collected', **m, villa=self.villa.pk)[1]['total'], self.paid_for(THIS_MONTH, invoice__partition__villa=self.villa))
        self.assertEqual(self.bar('collected', **m, villa=self.villa_b.pk)[1]['total'], Decimal('700.00'))
        self.assertEqual(self.bar('collected', **m, collector='admin')[1]['total'], self.paid_for(THIS_MONTH, collected_by__is_owner=True))
        self.assertEqual(self.bar('collected', **m, collector='staff')[1]['total'], Decimal('1000.00'))
        self.assertEqual(self.bar('collected', **m, collector=str(self.staff.pk))[1]['total'], Decimal('1000.00'))
        self.assertEqual(self.bar('collected', **m, q='T 101')[1]['total'], self.paid_for(THIS_MONTH, invoice__partition=self.p1))
        self.assertEqual(self.bar('collected', **m, villa=self.villa.pk, collector='staff')[1]['total'], Decimal('1000.00'))  # filters combine
        _, none = self.bar('collected', **m, q='no such tenant')
        self.assertEqual((none['total'], none['count']), (ZERO, 0))

    def test_the_filtered_state_is_shown_and_an_unfiltered_page_says_all(self):
        r, _ = self.bar('collected', **self.month(), collector='staff', villa=self.villa.pk)
        self.assertContains(r, 'Filtered')
        self.assertEqual(r.context['filter_summary'][0], 'Staff collections')
        self.assertIn('Villa', r.context['filter_summary'])
        r, _ = self.bar('collected', **self.month())
        self.assertEqual(r.context['filter_summary'], [])

    def test_the_whole_year_adds_every_month_and_months_never_leak(self):
        _, this = self.bar('collected', **self.month())
        _, last = self.bar('collected', **self.month(LAST_MONTH))
        _, year = self.bar('collected', period='year', year=THIS_MONTH.year)
        self.assertEqual(year['total'], this['total'] + last['total'])

    def test_a_reverted_collection_leaves_the_total_by_exactly_its_amount(self):
        _, before = self.bar('collected', **self.month())
        payment = Payment.objects.filter(is_cancelled=False, invoice__partition=self.p1, invoice__billing_period_start=THIS_MONTH).first()
        cancel_payment(payment=payment, cancelled_by=self.owner, reason='mistake')
        _, after = self.bar('collected', **self.month())
        self.assertEqual((before['total'] - after['total'], before['count'] - after['count']), (payment.amount, 1))


class RentTabsBarTests(TotalBarBase):

    def invoices(self):
        return Invoice.objects.filter(billing_period_start=THIS_MONTH, is_cancelled=False)

    def test_due_collected_and_pending_match_the_invoices_of_the_month(self):
        r, b = self.bar('all', **self.month())
        due = sum((bs.invoice_total(i) for i in self.invoices()), ZERO)
        paid = self.paid_for(THIS_MONTH)
        self.assertEqual((b['due'], b['rent_collected'], b['pending']), (due, paid, due - paid))
        self.assertEqual(b['count'], len(r.context['collection_rows']))
        self.assertContains(r, 'data-total-bar="rent"')

    def test_the_bar_agrees_with_the_header_figures_on_the_page_when_no_collector_is_chosen(self):
        r, b = self.bar('all', **self.month())
        c = r.context
        self.assertEqual((b['due'], b['rent_collected'], b['pending']), (c['portfolio_rent_expected'], c['portfolio_rent_collected'], c['portfolio_rent_pending']))

    def test_earlier_months_still_unpaid_are_totalled_and_counted(self):
        r, b = self.bar('all', **self.month())
        earlier = Invoice.objects.filter(billing_period_start__lt=THIS_MONTH, is_cancelled=False)
        unpaid = sum((max(ZERO, bs.invoice_total(i) - bs.invoice_paid_amount(i)) for i in earlier), ZERO)
        self.assertEqual(b['arrears'], unpaid)
        self.assertGreater(b['arrears'], 0)
        self.assertEqual(b['arrears_count'], len(r.context['arrears_rows']))
        self.assertEqual(b['count'] + b['arrears_count'], r.context['tab_counts']['all'])  # the tab badge counts both sections
        self.assertContains(r, 'Earlier months unpaid')

    def test_the_pending_tab_totals_only_what_is_still_pending(self):
        r, b = self.bar('pending', **self.month())
        rows = r.context['collection_rows']
        self.assertEqual(b['pending'], sum((max(ZERO, x['due'] - x['paid']) for x in rows), ZERO))
        self.assertEqual(b['count'] + b['arrears_count'], r.context['tab_counts']['pending'])
        self.assertTrue(all(x['status'] != 'collected' for x in rows))

    def test_a_collector_filter_shows_what_that_collector_collected_and_the_real_pending(self):
        self.assertEqual(self.bar('all', **self.month(), collector='staff')[1]['rent_collected'], Decimal('1000.00'))
        self.assertEqual(self.bar('all', **self.month(), collector='admin')[1]['rent_collected'], self.paid_for(THIS_MONTH, collected_by__is_owner=True))

    def test_the_villa_filter_and_search_narrow_the_rows(self):
        _, a = self.bar('all', **self.month(), villa=self.villa.pk)
        self.assertEqual((a['due'], a['rent_collected']), (RENT * 2, self.paid_for(THIS_MONTH, invoice__partition__villa=self.villa)))
        _, b = self.bar('all', **self.month(), villa=self.villa_b.pk)
        self.assertEqual((b['due'], b['rent_collected'], b['pending']), (RENT, Decimal('700.00'), RENT - Decimal('700.00')))
        _, one = self.bar('all', **self.month(), q='T 101')
        self.assertEqual((one['count'], one['due']), (1, RENT))

    def test_a_reverted_collection_goes_back_into_pending_and_due_does_not_move(self):
        _, before = self.bar('all', **self.month())
        payment = Payment.objects.filter(is_cancelled=False, invoice__partition=self.p1, invoice__billing_period_start=THIS_MONTH).first()
        cancel_payment(payment=payment, cancelled_by=self.owner, reason='mistake')
        _, after = self.bar('all', **self.month())
        self.assertEqual((after['pending'] - before['pending'], before['rent_collected'] - after['rent_collected'], after['due']), (payment.amount, payment.amount, before['due']))

    def test_another_months_collections_never_change_this_months_figures(self):
        _, this = self.bar('all', **self.month())
        _, last = self.bar('all', **self.month(LAST_MONTH))
        self.assertEqual(this['rent_collected'], self.paid_for(THIS_MONTH))
        self.assertEqual(last['rent_collected'], self.paid_for(LAST_MONTH))

    def test_the_yearly_rent_tabs_have_no_rent_sheet_so_no_bar(self):
        r = self.client.get(reverse('billing:payment_list'), {'tab': 'all', 'period': 'year', 'year': THIS_MONTH.year})
        self.assertIsNone(r.context['totals_bar'])
        self.assertNotContains(r, 'data-total-bar')

    def test_the_bar_is_sticky_markup_at_the_end_of_the_page_content(self):
        r = self.client.get(reverse('billing:payment_list'), {'tab': 'all', **self.month()})
        html = r.content.decode()
        self.assertLess(html.index('Earlier months still unpaid'), html.index('data-total-bar'))  # after the lists it totals
        self.assertContains(r, 'collections-total-bar')


class LiveSyncTests(TotalBarBase):
    """What static/js/live.js relies on: a money action posted in the background hands back its redirect (message kept for the
    refresh), and re-fetching the page gives the new totals, lists and message, with nothing counted twice."""

    BG = {'HTTP_X_MODAL': '1', 'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}

    def page_url(self, **extra):
        return reverse('billing:payment_list') + '?' + '&'.join(f'{k}={v}' for k, v in {'tab': 'all', **self.month(), **extra}.items())

    def refresh(self, url):
        """The live refresh: fetch the same page again."""
        r = self.client.get(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200)
        return r

    def message_area(self, r):
        import re
        return re.search(r'data-live="messages">(.*)', r.content.decode()).group(1).split('\n')[0]

    def test_every_live_part_is_on_the_page(self):
        html = self.refresh(self.page_url()).content.decode()
        for name in ('summary', 'tabs', 'list', 'total', 'messages'):
            self.assertEqual(html.count(f'data-live="{name}"'), 1, name)

    def test_a_rent_collection_updates_the_total_the_lists_and_shows_its_message_without_a_page_reload(self):
        url = self.page_url()
        before = self.refresh(url).context['totals_bar']
        r = self.client.post(reverse('billing:rent_collect', args=[self.p2.pk]), {'year': THIS_MONTH.year, 'month': THIS_MONTH.month, 'method': 'cash', 'amount': '500', 'next': url}, **self.BG)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['X-Modal-Redirect'], url)                       # handed back, not followed: the message is kept for the refresh
        after_r = self.refresh(url)
        after = after_r.context['totals_bar']
        self.assertEqual(after['rent_collected'] - before['rent_collected'], Decimal('500.00'))
        self.assertEqual(before['pending'] - after['pending'], Decimal('500.00'))
        self.assertEqual(after['due'], before['due'])
        self.assertIn('collected for', self.message_area(after_r))          # the success message shows in the refreshed message area
        self.assertNotIn('collected for', self.message_area(self.refresh(url)))  # shown once, not on every refresh

    def test_an_advance_collection_is_refreshed_the_same_way(self):
        url = reverse('billing:payment_list') + f'?tab=collected&period=month&year={LAST_MONTH.year}&month={LAST_MONTH.month}'
        tenant = self.p2.current_tenant
        update_tenant(tenant=tenant, updated_by=self.owner, deposit=2000)
        mid = self.refresh(url).context['totals_bar']
        r = self.client.post(reverse('tenancy:deposit_edit', args=[tenant.pk]), {'deposit': '2000.00', 'amount': '600.00', 'method': 'cash', 'collected_at': THIS_MONTH.isoformat(), 'next': url}, **self.BG)
        self.assertEqual(r['X-Modal-Redirect'], url)
        after_r = self.refresh(url)
        after = after_r.context['totals_bar']
        self.assertEqual(after['total'] - mid['total'], Decimal('600.00'))   # the advance counts in its move-in month (last month)
        self.assertEqual(after['advance'] - mid['advance'], Decimal('600.00'))
        self.assertIn('Advance for', self.message_area(after_r))

    def test_a_refused_collection_changes_nothing_and_its_error_shows_in_the_message_area(self):
        url = self.page_url()
        before = self.refresh(url).context['totals_bar']
        r = self.client.post(reverse('billing:rent_collect', args=[self.p1.pk]), {'year': THIS_MONTH.year, 'month': THIS_MONTH.month, 'method': 'cash', 'amount': '100', 'next': url}, **self.BG)  # already fully paid
        self.assertEqual(r['X-Modal-Redirect'], url)
        after_r = self.refresh(url)
        self.assertEqual({k: v for k, v in after_r.context['totals_bar'].items() if k != 'count'}, {k: v for k, v in before.items() if k != 'count'})
        self.assertIn('alert--error', self.message_area(after_r))

    def test_a_revert_from_this_page_returns_here_and_the_refresh_shows_the_lower_total(self):
        url = self.page_url(tab='collected')
        before = self.refresh(url).context['totals_bar']
        payment = Payment.objects.filter(is_cancelled=False, invoice__partition=self.p1, invoice__billing_period_start=THIS_MONTH).first()
        from urllib.parse import quote
        r = self.client.post(f"{reverse('billing:payment_cancel', args=[payment.pk])}?next={quote(url, safe='')}", {'reason': 'accidental click'}, **self.BG)  # encoded, as the Revert link does
        self.assertEqual(r['X-Modal-Redirect'], url)
        after_r = self.refresh(url)
        self.assertEqual(before['total'] - after_r.context['totals_bar']['total'], payment.amount)
        self.assertIn('reverted', self.message_area(after_r))

    def test_refreshing_has_no_side_effects_and_gives_the_same_figures_every_time(self):
        url = self.page_url()
        rows_before = Payment.objects.count()
        first, second = self.refresh(url).context['totals_bar'], self.refresh(url).context['totals_bar']
        self.assertEqual(first, second)
        self.assertEqual(Payment.objects.count(), rows_before)  # fetching the page never records anything


class AdvanceColumnTests(TotalBarBase):

    def test_a_part_collected_advance_shows_what_was_collected_as_well_as_what_is_pending(self):
        r = self.client.get(reverse('billing:payment_list'), {'tab': 'all', **self.month(LAST_MONTH)})
        html = r.content.decode()
        self.assertIn('Collected: QAR 1,200.00', html)   # p1's advance: 1200 of 3000 collected
        self.assertIn('Pending QAR 1,800', html)

    def test_total_collected_includes_the_advance_recorded_in_the_selected_month_only(self):
        # p1's 1200 advance was recorded this month (its tenant moved in last month).
        this = self.client.get(reverse('billing:payment_list'), {'tab': 'all', **self.month(THIS_MONTH)})
        bar = this.context['totals_bar']
        self.assertEqual(bar['advance_collected'], Decimal('1200.00'))
        self.assertEqual(bar['collected'], bar['rent_collected'] + Decimal('1200.00'))
        self.assertContains(this, '(Adv: QAR 1,200.00)')
        self.assertNotContains(this, 'Advance collected')
        last = self.client.get(reverse('billing:payment_list'), {'tab': 'all', **self.month(LAST_MONTH)})
        self.assertEqual(last.context['totals_bar']['advance_collected'], ZERO)   # not counted in another month
        self.assertEqual(last.context['totals_bar']['collected'], last.context['totals_bar']['rent_collected'])
        self.assertNotContains(last, '(Adv:')
