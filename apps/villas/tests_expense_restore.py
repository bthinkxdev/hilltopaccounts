"""Cancel takes an unpaid expense out of the totals; Restore brings it back; Revert payment only unpays. Reverse is no longer offered."""
from decimal import Decimal

from django.urls import reverse

from apps.expenses.models import Expense
from apps.expenses.services import cancel_expense, create_expense, get_or_create_category
from .tests_villa_stats import THIS_MONTH, VillaStatsBase


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
