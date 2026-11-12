"""Cancel takes an unpaid expense out of the totals; Restore brings it back; Revert payment only unpays. Reverse is no longer offered."""
from decimal import Decimal

from django.urls import reverse

from apps.expenses.models import Expense
from apps.expenses.services import cancel_expense, create_expense, get_or_create_category
from .tests_villa_stats import LAST_MONTH, THIS_MONTH, TODAY, VillaStatsBase


class RestoreTests(VillaStatsBase):

    def setUp(self):
        super().setUp()
        self.login('owner')
        self.expense = create_expense(villa=self.villa, category=get_or_create_category(name='Plumbing'), amount=Decimal('150.00'), date=THIS_MONTH, created_by=self.owner)

    def total(self):
        from apps.expenses.selectors import valid_expense_total
        return valid_expense_total(Expense.objects.filter(villa=self.villa))

    def test_cancel_removes_it_from_the_totals_and_restore_brings_it_back_editable_and_payable(self):
        before = self.total()
        cancel_expense(expense=self.expense, cancelled_by=self.owner, reason='entered twice')
        self.assertEqual(self.total(), before - Decimal('150.00'))
        r = self.client.post(reverse('expenses:restore', args=[self.expense.pk]), {'reason': 'it was needed after all'})
        self.assertEqual(r.status_code, 302)
        self.expense.refresh_from_db()
        self.assertEqual((self.expense.status, self.expense.paid_on, self.expense.status_reason), ('active', None, ''))
        self.assertEqual(self.total(), before)
        r = self.client.post(reverse('expenses:mark_paid', args=[self.expense.pk]), {'payment_method': 'cash', 'paid_on': THIS_MONTH.isoformat()})
        self.expense.refresh_from_db()
        self.assertIsNotNone(self.expense.paid_on)

    def test_a_reason_is_required_and_only_a_cancelled_expense_can_be_restored(self):
        cancel_expense(expense=self.expense, cancelled_by=self.owner, reason='x')
        self.client.post(reverse('expenses:restore', args=[self.expense.pk]), {'reason': ''})
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.status, 'cancelled')
        self.client.post(reverse('expenses:restore', args=[self.expense.pk]), {'reason': 'ok'})
        self.client.post(reverse('expenses:restore', args=[self.expense.pk]), {'reason': 'again'})   # already active: refused, nothing changes
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.status, 'active')

    def test_staff_cannot_restore(self):
        cancel_expense(expense=self.expense, cancelled_by=self.owner, reason='x')
        self.login('staff')
        r = self.client.post(reverse('expenses:restore', args=[self.expense.pk]), {'reason': 'try'})
        self.assertIn(r.status_code, (403, 404))
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.status, 'cancelled')

    def test_the_list_offers_cancel_only_on_unpaid_expenses_restore_on_cancelled_ones_and_no_reverse(self):
        html = self.client.get(reverse('expenses:list'), {'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month}).content.decode()
        self.assertIn(reverse('expenses:cancel', args=[self.expense.pk]), html)
        self.assertNotIn('>Reverse<', html)
        cancel_expense(expense=self.expense, cancelled_by=self.owner, reason='x')
        html = self.client.get(reverse('expenses:list'), {'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month, 'status': 'cancelled'}).content.decode()
        self.assertIn(reverse('expenses:restore', args=[self.expense.pk]), html)
        self.assertNotIn(reverse('expenses:cancel', args=[self.expense.pk]), html)

    def test_a_cancelled_expense_has_no_pay_tick_box_on_the_villa_page_or_the_list(self):
        cancel_expense(expense=self.expense, cancelled_by=self.owner, reason='x')
        villa = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), {'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month}).content.decode()
        lst = self.client.get(reverse('expenses:list'), {'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month, 'status': 'cancelled'}).content.decode()
        for html in (villa, lst):
            self.assertNotIn(f'name="expense_ids" value="{self.expense.pk}"', html)
        self.assertIn(f'/expenses/{self.expense.pk}/restore/', lst)


class MonthlyCancelTests(VillaStatsBase):
    """Cancelling a monthly expense: this month only, or forever (this month and every later one)."""

    def setUp(self):
        super().setUp()
        from apps.expenses.services import create_recurring_expense
        self.login('owner')
        self.rec = create_recurring_expense(villa=self.villa, category=get_or_create_category(name='Internet'), amount=Decimal('300.00'), due_day=1, created_by=self.owner, start_period=LAST_MONTH)
        self.this = Expense.objects.get(recurring=self.rec, period=THIS_MONTH)
        self.last = Expense.objects.get(recurring=self.rec, period=LAST_MONTH)

    def cancel(self, expense, scope):
        return self.client.post(reverse('expenses:cancel', args=[expense.pk]), {'reason': 'not needed', 'scope': scope})

    def test_the_cancel_form_asks_month_or_forever_for_a_monthly_expense_only(self):
        html = self.client.get(reverse('expenses:cancel', args=[self.this.pk])).content.decode()
        self.assertIn('value="forever"', html)
        one_off = create_expense(villa=self.villa, category=get_or_create_category(name='Plumbing'), amount=Decimal('50.00'), date=THIS_MONTH, created_by=self.owner)
        self.assertNotIn('value="forever"', self.client.get(reverse('expenses:cancel', args=[one_off.pk])).content.decode())

    def test_this_month_only_keeps_the_monthly_expense_running(self):
        self.cancel(self.this, 'month')
        self.this.refresh_from_db(); self.rec.refresh_from_db()
        self.assertEqual(self.this.status, 'cancelled')
        self.assertTrue(self.rec.is_active)

    def test_forever_cancels_this_month_stops_the_expense_and_offers_nothing_later(self):
        from datetime import timedelta
        from apps.expenses.selectors import fixed_expense_checklist
        self.cancel(self.this, 'forever')
        self.this.refresh_from_db(); self.rec.refresh_from_db(); self.last.refresh_from_db()
        self.assertEqual(self.this.status, 'cancelled')
        self.assertFalse(self.rec.is_active)
        self.assertEqual(self.last.status, 'active')                                   # earlier months are untouched
        nxt = (THIS_MONTH + timedelta(days=40)).replace(day=1)
        rows, _ = fixed_expense_checklist(self.villa, nxt.year, nxt.month)
        self.assertEqual([r for r in rows if r['template'].pk == self.rec.pk], [])      # not listed: no Pay ahead
        self.assertEqual(Expense.objects.filter(recurring=self.rec, period__gt=THIS_MONTH).count(), 0)

    def test_forever_from_an_earlier_month_cancels_every_unpaid_month_from_there(self):
        self.cancel(self.last, 'forever')
        self.assertEqual(set(Expense.objects.filter(recurring=self.rec).values_list('status', flat=True)), {'cancelled'})

    def test_forever_is_refused_while_a_later_month_is_paid_and_changes_nothing(self):
        self.client.post(reverse('expenses:mark_paid', args=[self.this.pk]), {'payment_method': 'cash', 'paid_on': THIS_MONTH.isoformat()})
        self.this.refresh_from_db()
        self.assertIsNotNone(self.this.paid_on)
        self.cancel(self.last, 'forever')
        self.rec.refresh_from_db(); self.last.refresh_from_db()
        self.assertTrue(self.rec.is_active)
        self.assertEqual(self.last.status, 'active')

    def test_restoring_a_forever_cancelled_month_runs_the_monthly_expense_again(self):
        self.cancel(self.this, 'forever')
        self.client.post(reverse('expenses:restore', args=[self.this.pk]), {'reason': 'needed'})
        self.rec.refresh_from_db(); self.this.refresh_from_db()
        self.assertTrue(self.rec.is_active)
        self.assertEqual(self.this.status, 'active')


class KeepFiltersTests(VillaStatsBase):
    """An action taken in a popup on the Expenses page returns to the same page with its filters, not to the bare list."""

    def setUp(self):
        super().setUp()
        self.login('owner')
        self.expense = create_expense(villa=self.villa, category=get_or_create_category(name='Plumbing'), amount=Decimal('150.00'), date=THIS_MONTH, created_by=self.owner)
        self.page = f'/expenses/?villa={self.villa.pk}&due=unpaid&status=active&period=month&year={THIS_MONTH.year}&month={THIS_MONTH.month}'

    def test_the_list_links_carry_the_page_they_were_opened_from(self):
        html = self.client.get(self.page).content.decode()
        from urllib.parse import quote
        enc = quote(self.page, safe='/')
        for url in (reverse('expenses:cancel', args=[self.expense.pk]), reverse('expenses:revise', args=[self.expense.pk]), reverse('expenses:create')):
            self.assertIn(f'{url}?', html)
        self.assertGreaterEqual(html.count(f'next={enc}'), 3)

    def test_cancel_edit_and_mark_paid_return_to_the_filtered_page(self):
        r = self.client.post(reverse('expenses:revise', args=[self.expense.pk]), {'amount': '160.00', 'description': '', 'next': self.page})
        self.assertEqual(r['Location'], self.page)
        r = self.client.post(reverse('expenses:mark_paid', args=[self.expense.pk]), {'payment_method': 'cash', 'paid_on': THIS_MONTH.isoformat(), 'next': self.page})
        self.assertEqual(r['Location'], self.page)
        other = create_expense(villa=self.villa, category=get_or_create_category(name='Paint'), amount=Decimal('20.00'), date=THIS_MONTH, created_by=self.owner)
        r = self.client.post(reverse('expenses:cancel', args=[other.pk]), {'reason': 'x', 'next': self.page})
        self.assertEqual(r['Location'], self.page)

    def test_a_foreign_next_is_ignored(self):
        other = create_expense(villa=self.villa, category=get_or_create_category(name='Paint'), amount=Decimal('20.00'), date=THIS_MONTH, created_by=self.owner)
        r = self.client.post(reverse('expenses:cancel', args=[other.pk]), {'reason': 'x', 'next': 'https://evil.example/'})
        self.assertEqual(r['Location'], reverse('expenses:list'))


class ListFilterTests(VillaStatsBase):
    """The Expenses list shows exactly the rows each filter promises: status, payment state, villa and payer."""

    def setUp(self):
        super().setUp()
        self.login('owner')
        cat = lambda n: get_or_create_category(name=n)
        self.unpaid = create_expense(villa=self.villa, category=cat('Unpaid one'), amount=Decimal('10.00'), date=THIS_MONTH, created_by=self.owner)
        self.paid = create_expense(villa=self.villa, category=cat('Paid one'), amount=Decimal('20.00'), date=THIS_MONTH, created_by=self.owner)
        from apps.expenses.services import mark_expense_paid
        mark_expense_paid(expense=self.paid, marked_by=self.owner, payment_method='cash', paid_on=THIS_MONTH)
        self.gone = create_expense(villa=self.villa, category=cat('Gone one'), amount=Decimal('30.00'), date=THIS_MONTH, created_by=self.owner)
        cancel_expense(expense=self.gone, cancelled_by=self.owner, reason='no longer needed')
        self.base = f'/expenses/?period=month&year={THIS_MONTH.year}&month={THIS_MONTH.month}'

    def names(self, query=''):
        r = self.client.get(self.base + query)
        self.assertEqual(r.status_code, 200)
        return {e.category.name for e in r.context['page_obj']}

    def test_default_lists_active_only(self):
        self.assertEqual(self.names(), {'Unpaid one', 'Paid one'})

    def test_cancelled_filter_lists_cancelled_rows(self):
        self.assertEqual(self.names('&status=cancelled'), {'Gone one'})

    def test_cancelled_filter_is_not_emptied_by_a_payment_state(self):
        for due in ('unpaid', 'overdue', 'verify', 'paid'):
            self.assertEqual(self.names(f'&status=cancelled&due={due}'), {'Gone one'}, due)

    def test_all_lists_every_status(self):
        self.assertEqual(self.names('&status='), {'Unpaid one', 'Paid one', 'Gone one'})

    def test_payment_state_with_all_statuses_ignores_cancelled_rows(self):
        self.assertEqual(self.names('&status=&due=unpaid'), {'Unpaid one'})
        self.assertEqual(self.names('&status=&due=paid'), {'Paid one'})

    def test_payment_state_filters_on_active(self):
        self.assertEqual(self.names('&due=unpaid'), {'Unpaid one'})
        self.assertEqual(self.names('&due=paid'), {'Paid one'})
        self.assertEqual(self.names('&due=overdue'), set())

    def test_the_status_choice_stays_selected(self):
        for status in ('cancelled', ''):
            html = self.client.get(self.base + f'&status={status}').content.decode()
            self.assertIn('<option value="%s" selected>' % status, html)


class ExpenseTotalBarTests(VillaStatsBase):
    """The bar under the Expenses list totals exactly the rows the filters match, for the selected month and year."""

    def setUp(self):
        super().setUp()
        from apps.expenses.services import mark_expense_paid
        self.login('owner')
        cat = lambda n: get_or_create_category(name=n)
        self.unpaid = create_expense(villa=self.villa, category=cat('Unpaid one'), amount=Decimal('10.00'), date=THIS_MONTH, due_date=THIS_MONTH, created_by=self.owner)
        self.paid = create_expense(villa=self.villa, category=cat('Paid one'), amount=Decimal('20.00'), date=THIS_MONTH, created_by=self.owner)
        mark_expense_paid(expense=self.paid, marked_by=self.owner, payment_method='cash', paid_on=THIS_MONTH)
        self.admin_one = create_expense(villa=self.villa, category=cat('Admin one'), amount=Decimal('40.00'), date=THIS_MONTH, created_by=self.owner, assigned_to='owner')
        self.gone = create_expense(villa=self.villa, category=cat('Gone one'), amount=Decimal('30.00'), date=THIS_MONTH, created_by=self.owner)
        cancel_expense(expense=self.gone, cancelled_by=self.owner, reason='no longer needed')
        self.last = create_expense(villa=self.villa, category=cat('Last month'), amount=Decimal('500.00'), date=LAST_MONTH, created_by=self.owner)
        self.base = f'/expenses/?period=month&year={THIS_MONTH.year}&month={THIS_MONTH.month}'

    def bar(self, query=''):
        return self.client.get(self.base + query).context['totals_bar']

    def test_totals_are_for_the_selected_month_and_exclude_cancelled(self):
        bar = self.bar()
        self.assertEqual((bar['total'], bar['paid'], bar['unpaid'], bar['count']), (Decimal('70.00'), Decimal('20.00'), Decimal('50.00'), 3))
        self.assertEqual((bar['staff'], bar['owner']), (Decimal('30.00'), Decimal('40.00')))  # by assignment
        self.assertEqual(bar['staff'] + bar['owner'], bar['total'])
        last = self.client.get(f'/expenses/?period=month&year={LAST_MONTH.year}&month={LAST_MONTH.month}').context['totals_bar']
        self.assertEqual(last['total'], Decimal('500.00'))

    def test_the_bar_follows_the_filters(self):
        self.assertEqual(self.bar('&due=paid')['total'], Decimal('20.00'))
        self.assertEqual(self.bar('&due=unpaid')['total'], Decimal('50.00'))
        self.assertEqual(self.bar('&staff=owner')['total'], Decimal('40.00'))
        self.assertEqual(self.bar('&staff=staff')['total'], Decimal('30.00'))
        self.assertEqual(self.bar(f'&villa={self.villa.pk}')['total'], Decimal('70.00'))

    def test_cancelled_rows_are_totalled_apart(self):
        bar = self.bar('&status=cancelled')
        self.assertEqual((bar['kind'], bar['inactive'], bar['total']), ('inactive', Decimal('30.00'), Decimal('0.00')))
        everything = self.bar('&status=')
        self.assertEqual((everything['total'], everything['inactive'], everything['inactive_count']), (Decimal('70.00'), Decimal('30.00'), 1))

    def test_the_scope_text_names_the_filters(self):
        html = self.client.get(self.base + '&due=unpaid&staff=owner').content.decode()
        self.assertIn('collections-total-bar', html)
        self.assertIn('Outstanding', html)
        self.assertIn('Admin expenses', html)

    def test_overdue_counts_only_unpaid_rows_past_their_due_date(self):
        bar = self.bar()
        expected = (Decimal('10.00'), 1) if TODAY.day > 1 else (Decimal('0.00'), 0)
        self.assertEqual((bar['overdue'], bar['overdue_count']), expected)

    def test_the_bar_is_shown_to_staff_too(self):
        self.login('staff')
        self.assertEqual(self.bar()['total'], Decimal('70.00'))
        self.assertContains(self.client.get(self.base), 'Total expenses')
