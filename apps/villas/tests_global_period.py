"""The month/year choice is one global setting: the navbar shows it, the server remembers it for the session, and
every month-based page follows it. Pages that are deliberately not month-based ignore it."""
from datetime import date
from django.urls import reverse
from .tests_villa_stats import LAST_MONTH, THIS_MONTH, VillaStatsBase


class GlobalPeriodTests(VillaStatsBase):

    def setUp(self):
        super().setUp()
        self.login('owner')

    def test_navbar_shows_the_month_and_year_switcher_and_banner(self):
        r = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]))
        self.assertContains(r, 'data-period-switch')
        self.assertContains(r, 'id="period-month"')
        self.assertContains(r, 'id="period-year"')
        self.assertContains(r, f'Showing data for <strong>{THIS_MONTH:%B %Y}</strong>')
        self.assertContains(r, 'id="app-loader"')

    def test_picked_month_is_remembered_across_month_based_pages(self):
        self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), {'period': 'month', 'year': LAST_MONTH.year, 'month': LAST_MONTH.month})
        for url in (reverse('villas:villa_list'), reverse('expenses:list'), reverse('billing:payment_list'), reverse('dashboard:home')):
            r = self.client.get(url)
            self.assertContains(r, f'Showing data for <strong>{LAST_MONTH:%B %Y}</strong>', msg_prefix=url)
            self.assertEqual((r.context['year'], r.context['month']), (LAST_MONTH.year, LAST_MONTH.month), url)

    def test_year_view_is_remembered_and_shown_as_full_year(self):
        self.client.get(reverse('villas:villa_list'), {'period': 'year', 'year': THIS_MONTH.year})
        r = self.client.get(reverse('expenses:list'))
        self.assertEqual(r.context['period'], 'year')
        self.assertContains(r, f'Showing data for <strong>{THIS_MONTH.year}</strong>')

    def test_a_url_always_wins_over_the_remembered_month(self):
        self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': LAST_MONTH.year, 'month': LAST_MONTH.month})
        r = self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month})
        self.assertEqual((r.context['year'], r.context['month']), (THIS_MONTH.year, THIS_MONTH.month))

    def test_expenses_all_time_link_is_not_remembered(self):
        self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': LAST_MONTH.year, 'month': LAST_MONTH.month})
        self.client.get(reverse('expenses:list'), {'period': 'all'})
        r = self.client.get(reverse('villas:villa_list'))
        self.assertEqual((r.context['year'], r.context['month']), (LAST_MONTH.year, LAST_MONTH.month))

    def test_page_without_a_month_is_marked_idle_but_keeps_the_choice(self):
        self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': LAST_MONTH.year, 'month': LAST_MONTH.month})
        r = self.client.get(reverse('notifications:list'))
        self.assertContains(r, 'period-switch--idle')
        self.assertNotContains(r, 'period-banner"')
        self.assertContains(r, f'<option value="{LAST_MONTH.month}" selected>')

    def test_garbage_in_the_url_falls_back_to_the_current_month(self):
        r = self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': 'abc', 'month': '99'})
        today = date.today()
        self.assertEqual((r.context['year'], r.context['month']), (today.year, today.month))


class SelectedMonthDefaultsTests(VillaStatsBase):
    """Places with no period in the URL (new expense, payout, export, invoices) start from the navbar's month."""

    def setUp(self):
        super().setUp()
        self.login('owner')
        self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': LAST_MONTH.year, 'month': LAST_MONTH.month})

    def test_new_expense_date_defaults_into_the_selected_month(self):
        r = self.client.get(reverse('expenses:create'))
        self.assertEqual(r.context['form'].initial['date'], LAST_MONTH)

    def test_export_defaults_to_the_selected_month(self):
        import calendar
        r = self.client.get(reverse('dashboard:export_financials_csv'))
        self.assertEqual(r.status_code, 200)
        last_day = calendar.monthrange(LAST_MONTH.year, LAST_MONTH.month)[1]
        self.assertIn(f'{LAST_MONTH:%d %b %Y} to {LAST_MONTH.replace(day=last_day):%d %b %Y}', r.content.decode('utf-8-sig'))

    def test_invoice_list_follows_the_selected_month(self):
        r = self.client.get(reverse('billing:invoice_list'))
        self.assertContains(r, f'Showing data for <strong>{LAST_MONTH:%B %Y}</strong>')
        self.assertTrue(all(i.billing_period_start == LAST_MONTH for i in r.context['page_obj']))


class EveryRoleGetsTheSwitcherTests(VillaStatsBase):

    def test_owner_manager_and_staff_all_get_the_switcher_and_follow_it(self):
        for username in ('owner', 'manager', 'staff'):
            self.login(username)
            self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': LAST_MONTH.year, 'month': LAST_MONTH.month})
            for url in (reverse('villas:villa_list'), reverse('villas:villa_detail', args=[self.villa.pk]), reverse('expenses:list')):
                r = self.client.get(url)
                self.assertContains(r, 'data-period-switch', msg_prefix=f'{username} {url}')
                self.assertContains(r, f'Showing data for <strong>{LAST_MONTH:%B %Y}</strong>', msg_prefix=f'{username} {url}')
