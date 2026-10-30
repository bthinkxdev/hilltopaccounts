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


class QaRegressionTests(VillaStatsBase):
    """The four QA failures: every page follows the navbar month, a villa expense can be dated in a past month, and a
    charge can be added to an existing tenant even though the only charge type is Rent."""

    def setUp(self):
        super().setUp()
        self.login('owner')
        self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': LAST_MONTH.year, 'month': LAST_MONTH.month})

    def test_cash_handover_staff_and_audit_pages_follow_the_selected_month(self):
        for url in (reverse('cash:handover_list'), reverse('accounts:staff_list'), reverse('audit:list')):
            r = self.client.get(url)
            self.assertContains(r, f'Showing data for <strong>{LAST_MONTH:%B %Y}</strong>', msg_prefix=url)
            self.assertNotContains(r, 'period-switch--idle', msg_prefix=url)

    def test_handover_is_listed_under_the_month_of_its_cash(self):
        from apps.cash.models import CashHandover
        self.login('staff')
        self.collect(self.p1, LAST_MONTH)
        from apps.billing.models import Payment
        self.client.post(reverse('cash:handover_submit'), {'payments': [p.pk for p in Payment.objects.filter(collected_by=self.staff)]})
        h = CashHandover.objects.first()
        self.assertIsNotNone(h)
        self.login('owner')
        listed = lambda m: [x.pk for x in self.client.get(reverse('cash:handover_list'), {'period': 'month', 'year': m.year, 'month': m.month}).context['page_obj']]
        self.assertIn(h.pk, listed(LAST_MONTH))

    def test_villa_page_add_expense_opens_a_dated_expense_in_the_selected_month(self):
        r = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]))
        self.assertContains(r, reverse('expenses:create_for_villa', args=[self.villa.pk]))
        r = self.client.get(reverse('expenses:create_for_villa', args=[self.villa.pk]))
        self.assertEqual(r.context['form'].initial['date'], LAST_MONTH)

    def test_expense_can_be_added_for_a_past_month(self):
        from apps.expenses.models import Expense
        r = self.client.post(reverse('expenses:create_for_villa', args=[self.villa.pk]), {'villa': self.villa.pk, 'name': 'Cleaning', 'amount': '50.00', 'date': LAST_MONTH.isoformat()})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Expense.objects.get(villa=self.villa).date, LAST_MONTH)

    def test_charge_can_be_added_to_a_tenant_who_moved_in_earlier(self):
        from apps.billing.models import Charge, ChargeType
        ChargeType.objects.exclude(name='Rent').delete()
        url = reverse('billing:charge_create', args=[self.p1.pk])
        self.assertContains(self.client.get(reverse('villas:partition_detail', args=[self.p1.pk])), url)
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {'new_charge_type': 'Electricity', 'amount': '75.00', 'frequency': 'monthly', 'start_date': LAST_MONTH.isoformat()})
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['form'].errors)
        self.assertTrue(Charge.objects.filter(partition=self.p1, charge_type__name='Electricity').exists())
        # the same type again is refused, and Rent cannot be added as a charge
        r = self.client.post(url, {'new_charge_type': 'electricity', 'amount': '10', 'frequency': 'monthly', 'start_date': LAST_MONTH.isoformat()})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(url, {'new_charge_type': 'Rent', 'amount': '10', 'frequency': 'monthly', 'start_date': LAST_MONTH.isoformat()})
        self.assertEqual(r.status_code, 200)

    def test_villa_page_lists_one_time_expenses_with_the_bulk_pay_option(self):
        for name, partition in (('Water tanker', ''), ('Door lock', self.p1.pk)):
            self.client.post(reverse('expenses:create_for_villa', args=[self.villa.pk]), {'villa': self.villa.pk, 'partition': partition, 'name': name, 'amount': '80.00', 'date': LAST_MONTH.isoformat()})
        r = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]))
        self.assertEqual(sorted(e.category.name for e in r.context['one_off_expenses']), ['Door lock', 'Water tanker'])
        self.assertContains(r, 'One-time expenses')
        self.assertContains(r, 'Whole villa')
        self.assertContains(r, 'name="expense_ids"')  # the select-to-pay checkboxes are back for whole-villa expenses

    def test_add_expense_can_be_monthly_repeated_from_a_past_month(self):
        from apps.expenses.models import Expense, RecurringExpense
        url = reverse('expenses:create_for_villa', args=[self.villa.pk])
        self.assertContains(self.client.get(url), 'name="frequency"')
        r = self.client.post(url, {'frequency': 'monthly', 'villa': self.villa.pk, 'name': 'Wi-Fi', 'amount': '300.00', 'date': LAST_MONTH.isoformat()})
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['form'].errors)
        rec = RecurringExpense.objects.get(villa=self.villa, category__name='Wi-Fi')
        self.assertEqual(rec.first_period, LAST_MONTH.replace(day=1))
        # it shows in the selected month's checklist and not in an earlier month
        self.assertEqual([x['template'].pk for x in self.page(LAST_MONTH)['fixed_rows']], [rec.pk])
        earlier = (LAST_MONTH.replace(day=1) - __import__('datetime').timedelta(days=1)).replace(day=1)
        self.assertEqual(self.page(earlier)['fixed_rows'], [])
        # a one-time expense is untouched by the monthly choice
        self.client.post(url, {'frequency': 'one_time', 'villa': self.villa.pk, 'name': 'Door lock', 'amount': '80', 'date': LAST_MONTH.isoformat()})
        self.assertEqual(Expense.objects.filter(villa=self.villa, recurring__isnull=True, category__name='Door lock').count(), 1)

    def test_monthly_expense_cannot_be_a_partition_expense_or_a_duplicate(self):
        url = reverse('expenses:create_for_villa', args=[self.villa.pk])
        base = {'frequency': 'monthly', 'villa': self.villa.pk, 'name': 'Cleaning', 'amount': '100', 'date': LAST_MONTH.isoformat()}
        self.assertEqual(self.client.post(url, {**base, 'partition': self.p1.pk}).status_code, 200)
        self.assertEqual(self.client.post(url, base).status_code, 302)
        self.assertEqual(self.client.post(url, base).status_code, 200)
