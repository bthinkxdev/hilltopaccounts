"""Cancel takes an unpaid expense out of the totals; Restore brings it back; Revert payment only unpays. Reverse is no longer offered."""
from decimal import Decimal

from django.urls import reverse

from apps.expenses.models import Expense
from apps.expenses.services import cancel_expense, create_expense, get_or_create_category
from .tests_villa_stats import LAST_MONTH, THIS_MONTH, VillaStatsBase


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
