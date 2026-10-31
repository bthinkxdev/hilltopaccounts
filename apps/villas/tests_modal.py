"""Action pages open as popups. The popup is only another way to show and submit the same server view: the data
saved, the validation and the messages must be identical to the page-based flow."""
from django.urls import reverse
from .tests_villa_stats import LAST_MONTH, VillaStatsBase

MODAL = {'HTTP_X_MODAL': '1'}


class ActionPopupTests(VillaStatsBase):

    def setUp(self):
        super().setUp()
        self.login('owner')
        self.url = reverse('expenses:create_for_villa', args=[self.villa.pk])
        self.good = {'villa': self.villa.pk, 'name': 'Door lock', 'amount': '80.00', 'date': LAST_MONTH.isoformat()}

    def test_modal_request_gets_a_bare_fragment_with_a_cancel(self):
        r = self.client.get(self.url, **MODAL)
        self.assertContains(r, 'data-modal-fragment')
        self.assertContains(r, 'data-modal-cancel')
        self.assertNotContains(r, '<html')
        self.assertNotContains(r, 'class="sidebar"')

    def test_normal_request_is_the_full_page_as_before(self):
        r = self.client.get(self.url)
        self.assertContains(r, 'class="sidebar"')
        self.assertNotContains(r, 'data-modal-fragment')
        self.assertIn('X-Modal', r['Vary'])

    def test_valid_post_saves_and_hands_the_redirect_back_without_losing_the_message(self):
        from apps.expenses.models import Expense
        r = self.client.post(self.url, self.good, **MODAL)
        self.assertEqual(r.status_code, 200)
        target = r['X-Modal-Redirect']
        self.assertEqual(Expense.objects.filter(villa=self.villa, category__name='Door lock').count(), 1)
        self.assertContains(self.client.get(target), 'Door lock')  # the success message is still waiting for the next page

    def test_invalid_post_comes_back_as_the_form_with_errors_and_saves_nothing(self):
        from apps.expenses.models import Expense
        r = self.client.post(self.url, {**self.good, 'amount': '0'}, **MODAL)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('X-Modal-Redirect', r)
        self.assertContains(r, 'data-modal-fragment')
        self.assertContains(r, 'field__error')
        self.assertEqual(Expense.objects.filter(villa=self.villa).count(), 0)

    def test_the_same_post_without_the_popup_still_redirects_and_saves_the_same_row(self):
        from apps.expenses.models import Expense
        r = self.client.post(self.url, self.good)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Expense.objects.get(villa=self.villa, category__name='Door lock').amount, 80)

    def test_every_kind_of_action_page_renders_as_a_fragment(self):
        tenant = self.p1.current_tenant
        urls = [reverse('tenancy:move_out', args=[tenant.pk]), reverse('tenancy:edit', args=[tenant.pk]), reverse('tenancy:deposit_edit', args=[tenant.pk]),
                reverse('billing:charge_create', args=[self.p1.pk]), reverse('villas:villa_edit', args=[self.villa.pk]), reverse('villas:partition_create'),
                reverse('villas:villa_sell', args=[self.villa.pk]), reverse('villas:villa_archive', args=[self.villa.pk]), reverse('villas:villa_photo_add', args=[self.villa.pk]),
                reverse('businesses:create'), reverse('businesses:partner_create'), reverse('accounts:staff_create'), reverse('accounts:password_change'),
                reverse('expenses:create'), reverse('expenses:recurring_create', args=[self.villa.pk]), reverse('businesses:villa_partner_payout_batch', args=[self.villa.pk])]
        for url in urls:
            r = self.client.get(url, **MODAL)
            if r.status_code == 200 and 'X-Modal-Redirect' in r:
                continue  # a view that sends the user elsewhere (e.g. nothing to do) hands that back instead
            self.assertEqual(r.status_code, 200, url)
            self.assertContains(r, 'data-modal-fragment', msg_prefix=url)
            self.assertContains(r, 'data-modal-cancel', msg_prefix=url)

    def test_pages_carry_the_popup_shell_and_mark_their_action_links(self):
        r = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]))
        self.assertContains(r, 'id="action-modal"')
        self.assertContains(r, 'js/modal.js')
        self.assertContains(r, f'href="{reverse("expenses:create_for_villa", args=[self.villa.pk])}" data-modal')

    def test_a_permission_failure_is_not_turned_into_a_popup(self):
        self.login('staff')
        r = self.client.get(reverse('businesses:create'), **MODAL)
        self.assertEqual(r.status_code, 403)


class ActionListTests(VillaStatsBase):

    def test_the_action_page_list_matches_the_routes_that_really_render_an_action_template(self):
        import inspect
        from django.urls import URLPattern, URLResolver, get_resolver
        from apps.shared.modal import ACTION_TEMPLATES, ACTION_URL_NAMES
        found = set()

        def walk(resolver):
            for p in resolver.url_patterns:
                if isinstance(p, URLResolver):
                    walk(p)
                elif isinstance(p, URLPattern) and any(t in inspect.getsource(p.callback) for t in ACTION_TEMPLATES):
                    found.add(f'{resolver.namespace}:{p.name}')
        walk(get_resolver())
        self.assertEqual(found, set(ACTION_URL_NAMES))

    def test_alert_and_dashboard_links_to_action_pages_open_as_popups(self):
        from apps.shared.modal import is_action_url
        self.assertTrue(is_action_url(reverse('expenses:mark_paid', args=[1]) + '?next=/'))
        self.assertTrue(is_action_url(reverse('cash:handover_submit')))
        self.assertFalse(is_action_url(reverse('expenses:list') + '?x=1'))
        self.assertFalse(is_action_url('https://elsewhere.example/expenses/new/'))
        self.assertFalse(is_action_url('#'))
        self.login('owner')
        r = self.client.get(reverse('dashboard:home'))
        self.assertContains(r, f'href="{reverse("expenses:create")}?next={reverse("dashboard:home")}" data-modal')


class BusinessesAdminOnlyTests(VillaStatsBase):
    """Businesses is an Admin-only section: no menu item, card, search result or page for anyone else."""

    def setUp(self):
        super().setUp()
        from apps.accounts.models import User
        from apps.accounts.services import assign_partner
        from apps.businesses.models import Partner
        self.partner_user = User.objects.create_user('partner1', password='x-Pass-1234-y')
        assign_partner(user=self.partner_user, villa=self.villa, assigned_by=self.owner, partner=Partner.objects.create(name='P1', created_by=self.owner))

    def test_nobody_but_the_admin_gets_the_menu_card_or_pages(self):
        for who in ('manager', 'staff', 'partner1'):
            self.client.logout()
            self.client.force_login(self.partner_user) if who == 'partner1' else self.login(who)
            home = self.client.get(reverse('dashboard:home'))
            self.assertNotContains(home, f'href="{reverse("businesses:list")}"', msg_prefix=who)
            self.assertNotContains(home, 'View all businesses', msg_prefix=who)
            self.assertEqual(self.client.get(reverse('businesses:list')).status_code, 403, who)
            self.assertEqual(self.client.get(reverse('businesses:detail', args=[self.business.pk])).status_code, 403, who)

    def test_the_admin_keeps_everything(self):
        self.login('owner')
        self.assertContains(self.client.get(reverse('villas:villa_list')), f'href="{reverse("businesses:list")}"')
        self.assertEqual(self.client.get(reverse('businesses:list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('businesses:detail', args=[self.business.pk])).status_code, 200)

    def test_a_manager_is_not_sent_to_a_blocked_page_from_breadcrumbs_or_search(self):
        self.login('manager')
        for url in (reverse('billing:charge_create', args=[self.p1.pk]), reverse('villas:partition_create', ) + f'?villa={self.villa.pk}', reverse('villas:partition_edit', args=[self.p1.pk])):
            r = self.client.get(url)
            self.assertNotContains(r, f'href="{reverse("businesses:list")}"', msg_prefix=url)
        self.assertNotContains(self.client.get(reverse('dashboard:search'), {'q': 'Biz'}), reverse('businesses:detail', args=[self.business.pk]))


class PayFutureExpenseTests(VillaStatsBase):
    """A monthly repeated expense can be paid before its month starts: the record is created and paid together, stays
    in its own month, and nothing is left half-done when it cannot be paid."""

    def setUp(self):
        super().setUp()
        from datetime import date, timedelta
        from apps.expenses.models import RecurringExpense
        from apps.expenses.services import create_recurring_expense, get_or_create_category
        self.login('owner')
        self.rec = create_recurring_expense(villa=self.villa, category=get_or_create_category(name='Villa rent'), amount=1500, due_day=1, created_by=self.owner)
        today = date.today().replace(day=1)
        self.future = (today + timedelta(days=62)).replace(day=1)
        self.url = reverse('expenses:recurring_pay_ahead', args=[self.rec.pk, self.future.year, self.future.month])
        self.post = {'amount': '1500', 'paid_on': date.today().isoformat(), 'payment_method': 'cash'}

    def month_page(self, month):
        return self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), {'period': 'month', 'year': month.year, 'month': month.month})

    def test_future_month_shows_a_pay_ahead_option_and_no_record_yet(self):
        from apps.expenses.models import Expense
        r = self.month_page(self.future)
        self.assertContains(r, self.url)
        self.assertEqual(Expense.objects.filter(recurring=self.rec, period=self.future).count(), 0)

    def test_pay_ahead_creates_the_month_and_pays_it_in_its_own_month(self):
        from apps.expenses.models import Expense
        r = self.client.post(self.url, self.post)
        self.assertEqual(r.status_code, 302)
        e = Expense.objects.get(recurring=self.rec, period=self.future)
        self.assertEqual((e.amount, e.date, e.status), (1500, self.future, 'active'))
        self.assertIsNotNone(e.paid_on)
        self.assertEqual(self.month_page(self.future).context['pnl']['expenses'], 1500)
        self.assertFalse(Expense.objects.filter(recurring=self.rec, paid_on__isnull=False).exclude(period=self.future).exists())  # no other month is touched

    def test_paying_the_same_month_twice_is_refused_and_does_not_duplicate(self):
        from apps.expenses.models import Expense
        self.client.post(self.url, self.post)
        r = self.client.post(self.url, self.post)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Expense.objects.filter(recurring=self.rec, period=self.future).count(), 1)

    def test_too_far_ahead_or_before_the_start_is_refused_and_saves_nothing(self):
        from datetime import date
        from apps.expenses.models import Expense
        far = reverse('expenses:recurring_pay_ahead', args=[self.rec.pk, date.today().year + 3, 1])
        self.assertEqual(self.client.post(far, self.post).status_code, 200)
        before = reverse('expenses:recurring_pay_ahead', args=[self.rec.pk, date.today().year - 2, 1])
        self.assertEqual(self.client.post(before, self.post).status_code, 200)
        # neither refused request created a month or paid anything (the current month is generated by the app itself)
        self.assertFalse(Expense.objects.filter(recurring=self.rec, period__gt=date.today().replace(day=1)).exists())
        self.assertFalse(Expense.objects.filter(recurring=self.rec, paid_on__isnull=False).exists())

    def test_someone_who_may_not_pay_it_leaves_no_record_behind(self):
        from apps.expenses.models import Expense
        from apps.expenses.services import create_recurring_expense, get_or_create_category
        auto = create_recurring_expense(villa=self.villa, category=get_or_create_category(name='Owner debit'), amount=900, due_day=1, created_by=self.owner, auto_debit=True)
        self.login('staff')
        url = reverse('expenses:recurring_pay_ahead', args=[auto.pk, self.future.year, self.future.month])
        r = self.client.post(url, self.post)
        self.assertIn(r.status_code, (200, 403))
        self.assertEqual(Expense.objects.filter(recurring=auto, period=self.future).count(), 0)

    def test_the_popup_prefills_the_scheduled_amount_and_a_changed_amount_applies_to_that_month_only(self):
        from decimal import Decimal
        from apps.expenses.models import Expense
        r = self.client.get(self.url)
        self.assertContains(r, 'name="amount"')
        self.assertEqual(r.context['form'].initial['amount'], self.rec.amount)
        self.assertEqual(self.client.post(self.url, {**self.post, 'amount': '1234.50'}).status_code, 302)
        e = Expense.objects.get(recurring=self.rec, period=self.future)
        self.assertEqual(e.amount, Decimal('1234.50'))
        self.assertIsNotNone(e.paid_on)
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.amount, 1500)  # the monthly expense itself is unchanged
        later = (self.future + __import__('datetime').timedelta(days=35)).replace(day=1)
        rows = self.month_page(later).context['fixed_rows']
        self.assertEqual(rows[0]['amount'], 1500)  # the next month still follows the scheduled amount

    def test_a_zero_or_missing_amount_is_refused_and_nothing_is_created_or_paid(self):
        from apps.expenses.models import Expense
        for bad in ('0', '', '-5'):
            self.assertEqual(self.client.post(self.url, {**self.post, 'amount': bad}).status_code, 200, bad)
        self.assertFalse(Expense.objects.filter(recurring=self.rec, period=self.future).exists())


class VillaFixedExpensesShowAtOnceTests(VillaStatsBase):
    """Adding the monthly amounts while editing a villa shows them in Expenses and in the totals on the very next page."""

    def test_monthly_amounts_from_villa_edit_are_in_expenses_straight_away(self):
        from datetime import date
        from apps.expenses.models import Expense
        self.login('owner')
        data = {'name': self.villa.name, 'fixed_villa_rent': '1000', 'fixed_electricity': '200', 'fixed_wifi': '50', 'fixed_cleaning': '60', 'fixed_maintenance': '70'}
        self.assertEqual(self.client.post(reverse('villas:villa_edit', args=[self.villa.pk]), data).status_code, 302)
        self.assertEqual(Expense.objects.filter(villa=self.villa, recurring__isnull=False, period=date.today().replace(day=1)).count(), 5)
        r = self.client.get(reverse('expenses:list'))  # the very first visit
        self.assertEqual(len(list(r.context['page_obj'])), 5)
        self.assertEqual(sum(e.amount for e in r.context['page_obj']), 1380)

    def test_a_monthly_expense_set_up_for_a_later_month_creates_nothing_yet(self):
        from datetime import date, timedelta
        from apps.expenses.models import Expense
        self.login('owner')
        later = (date.today().replace(day=1) + timedelta(days=62)).replace(day=1)
        self.client.post(reverse('expenses:create_for_villa', args=[self.villa.pk]), {'frequency': 'monthly', 'villa': self.villa.pk, 'name': 'Wi-Fi', 'amount': '300', 'date': later.isoformat()})
        self.assertFalse(Expense.objects.filter(villa=self.villa, recurring__isnull=False).exists())


class ScheduledMonthlyExpensesTests(VillaStatsBase):
    """Choosing a future month on the Expenses page shows the monthly expenses scheduled for it, without creating them."""

    def test_future_month_lists_scheduled_monthly_expenses_and_creates_nothing(self):
        from datetime import date, timedelta
        from apps.expenses.models import Expense
        from apps.expenses.services import create_recurring_expense, get_or_create_category
        self.login('owner')
        for name, amount in (('Villa rent', 699), ('Wi-Fi', 300)):
            create_recurring_expense(villa=self.villa, category=get_or_create_category(name=name), amount=amount, due_day=1, created_by=self.owner)
        future = (date.today().replace(day=1) + timedelta(days=35)).replace(day=1)
        before = Expense.objects.count()
        r = self.client.get(reverse('expenses:list'), {'period': 'month', 'year': future.year, 'month': future.month})
        self.assertEqual(sorted(s['template'].category.name for s in r.context['scheduled']), ['Villa rent', 'Wi-Fi'])
        self.assertEqual(r.context['scheduled_total'], 999)
        self.assertContains(r, 'Scheduled monthly expenses')
        self.assertContains(r, 'Pay ahead')
        self.assertEqual(Expense.objects.count(), before)  # viewing a future month records nothing

    def test_current_and_past_months_show_no_scheduled_block(self):
        from datetime import date
        self.login('owner')
        r = self.client.get(reverse('expenses:list'), {'period': 'month', 'year': date.today().year, 'month': date.today().month})
        self.assertEqual(r.context['scheduled'], [])


class MonthlyExpenseStartsInTheSelectedMonthTests(VillaStatsBase):
    """A monthly expense created while a month is selected in the navbar begins in that month: shown from then on, never
    before, and every month from its start up to now is already recorded (no page has to be opened first)."""

    def setUp(self):
        super().setUp()
        from datetime import date, timedelta
        self.login('owner')
        self.this_month = date.today().replace(day=1)
        self.next_month = (self.this_month + timedelta(days=35)).replace(day=1)
        self.two_back = (self.this_month - timedelta(days=40)).replace(day=1)
        self.before = (self.two_back - timedelta(days=5)).replace(day=1)

    def select(self, month):
        self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': month.year, 'month': month.month})

    def edit_villa(self):
        data = {'name': self.villa.name, 'fixed_villa_rent': '1000', 'fixed_wifi': '50'}
        self.assertEqual(self.client.post(reverse('villas:villa_edit', args=[self.villa.pk]), data).status_code, 302)

    def periods(self):
        from apps.expenses.models import Expense, RecurringExpense
        rec = RecurringExpense.objects.get(villa=self.villa, category__name='Wi-Fi')
        return rec, sorted(Expense.objects.filter(recurring=rec).values_list('period', flat=True))

    def test_edit_villa_in_a_future_month_starts_there_and_records_nothing_before(self):
        self.select(self.next_month)
        self.edit_villa()
        rec, periods = self.periods()
        self.assertEqual(rec.first_period, self.next_month)
        self.assertEqual(periods, [])  # that month has not begun yet
        for month in (self.this_month, self.two_back):
            rows = self.page(month)['fixed_rows']
            self.assertEqual(rows, [], month)

    def test_edit_villa_in_a_past_month_records_every_month_from_there_up_to_now(self):
        self.select(self.two_back)
        self.edit_villa()
        rec, periods = self.periods()
        self.assertEqual(rec.first_period, self.two_back)
        self.assertEqual(periods[0], self.two_back)
        self.assertEqual(periods[-1], self.this_month)
        self.assertEqual(len(periods), 3)
        self.assertEqual(self.page(self.before)['fixed_rows'], [])  # nothing before the start

    def test_add_expense_monthly_uses_its_date_and_the_old_monthly_form_uses_the_selected_month(self):
        self.select(self.next_month)
        url = reverse('expenses:recurring_create', args=[self.villa.pk])
        self.client.post(url, {'name': 'Cleaning', 'amount': '90'})
        from apps.expenses.models import RecurringExpense
        self.assertEqual(RecurringExpense.objects.get(villa=self.villa, category__name='Cleaning').first_period, self.next_month)
