from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from apps.accounts.models import User
from apps.accounts.selectors import expenses_visible_to
from apps.accounts.services import assign_villa_staff, assign_business_manager
from apps.audit.models import Action, AuditLog
from apps.businesses.services import create_business
from apps.villas.services import create_villa
from . import selectors
from .models import Expense
from .services import cancel_expense, create_expense, get_or_create_category, reverse_expense, update_expense

class ExpenseServiceTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', is_owner=True)
        self.business = create_business(name='Business A', created_by=self.owner)
        self.villa = create_villa(business=self.business, name='Villa 1', created_by=self.owner)
        self.category = get_or_create_category(name='Maintenance')

    def test_expense_requires_a_villa(self):
        with self.assertRaises(ValidationError):
            create_expense(villa=None, category=self.category, amount=Decimal('500.00'), date=date(2026, 3, 1), created_by=self.owner)

    def test_business_is_derived_from_villa_and_audited(self):
        expense = create_expense(villa=self.villa, category=self.category, amount=Decimal('300.00'), date=date(2026, 3, 1), payment_method='cash', created_by=self.owner)
        self.assertEqual(expense.business, self.business)
        entry = AuditLog.objects.get(action=Action.EXPENSE_CREATED)
        self.assertEqual(entry.villa, self.villa)
        self.assertEqual(entry.business, self.business)

    def test_user_outside_villa_scope_cannot_create_expense(self):
        from django.core.exceptions import PermissionDenied
        outsider = User.objects.create_user('outsider')
        with self.assertRaises(PermissionDenied):
            create_expense(villa=self.villa, category=self.category, amount=Decimal('10.00'), date=date(2026, 3, 1), created_by=outsider)

    def test_negative_amount_is_rejected(self):
        with self.assertRaises(ValidationError):
            create_expense(villa=self.villa, category=self.category, amount=Decimal('-50.00'), date=date(2026, 3, 1), payment_method='cash', created_by=self.owner)

    def test_zero_amount_is_rejected(self):
        with self.assertRaises(ValidationError):
            create_expense(villa=self.villa, category=self.category, amount=Decimal('0.00'), date=date(2026, 3, 1), payment_method='cash', created_by=self.owner)

    def test_update_captures_old_and_new_value(self):
        expense = create_expense(villa=self.villa, category=self.category, amount=Decimal('100.00'), date=date(2026, 3, 1), payment_method='cash', created_by=self.owner)
        update_expense(expense=expense, updated_by=self.owner, amount=Decimal('150.00'))
        entry = AuditLog.objects.get(action=Action.EXPENSE_UPDATED)
        self.assertEqual(entry.old_value['amount'], '100.00')
        self.assertEqual(entry.new_value['amount'], '150.00')

    def test_cancel_and_reverse_never_delete_the_row(self):
        expense = create_expense(villa=self.villa, category=self.category, amount=Decimal('100.00'), date=date(2026, 3, 1), payment_method='cash', created_by=self.owner)
        cancel_expense(expense=expense, cancelled_by=self.owner, reason='entered by mistake')
        expense.refresh_from_db()
        self.assertEqual(expense.status, Expense.Status.CANCELLED)
        self.assertTrue(Expense.objects.filter(pk=expense.pk).exists())

    def test_reverse_sets_reversed_status(self):
        expense = create_expense(villa=self.villa, category=self.category, amount=Decimal('100.00'), date=date(2026, 3, 1), payment_method='cash', created_by=self.owner)
        reverse_expense(expense=expense, reversed_by=self.owner, reason='double-counted')
        expense.refresh_from_db()
        self.assertEqual(expense.status, Expense.Status.REVERSED)

class ExpenseFinancialEngineTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', is_owner=True)
        self.business = create_business(name='Business A', created_by=self.owner)
        self.villa = create_villa(business=self.business, name='Villa 1', created_by=self.owner)
        self.category = get_or_create_category(name='Maintenance')

    def test_valid_expense_total_excludes_cancelled_and_reversed(self):
        e1 = create_expense(villa=self.villa, category=self.category, amount=Decimal('100.00'), date=date(2026, 3, 1), payment_method='cash', created_by=self.owner)
        e2 = create_expense(villa=self.villa, category=self.category, amount=Decimal('200.00'), date=date(2026, 3, 2), payment_method='cash', created_by=self.owner)
        create_expense(villa=self.villa, category=self.category, amount=Decimal('999.00'), date=date(2026, 3, 3), payment_method='cash', created_by=self.owner)
        cancel_expense(expense=e2, cancelled_by=self.owner, reason='mistake')
        third = Expense.objects.exclude(pk__in=[e1.pk, e2.pk]).get()
        reverse_expense(expense=third, reversed_by=self.owner, reason='undo')
        total = selectors.valid_expense_total(Expense.objects.filter(business=self.business))
        self.assertEqual(total, Decimal('100.00'))

class ExpenseRBACTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', is_owner=True)
        self.manager = User.objects.create_user('manager')
        self.staff = User.objects.create_user('staff')
        self.business = create_business(name='Business A', created_by=self.owner)
        self.villa = create_villa(business=self.business, name='Villa 1', created_by=self.owner)
        self.category = get_or_create_category(name='Maintenance')
        assign_business_manager(user=self.manager, business=self.business, assigned_by=self.owner)
        assign_villa_staff(user=self.staff, villa=self.villa, assigned_by=self.owner)
        # Legacy rows recorded before villas became mandatory may still be business-wide.
        self.business_level_expense = Expense.objects.create(business=self.business, category=self.category, amount=Decimal('500.00'), date=date(2026, 3, 1), payment_method='cash', created_by=self.owner)
        self.villa_level_expense = create_expense(villa=self.villa, category=self.category, amount=Decimal('100.00'), date=date(2026, 3, 1), payment_method='cash', created_by=self.owner)

    def test_manager_sees_both_business_and_villa_level_expenses(self):
        visible = expenses_visible_to(self.manager)
        self.assertEqual(set(visible), {self.business_level_expense, self.villa_level_expense})

    def test_staff_sees_villa_level_but_not_business_level_expense(self):
        visible = expenses_visible_to(self.staff)
        self.assertQuerySetEqual(visible, [self.villa_level_expense], transform=lambda e: e)

    def test_owner_sees_everything(self):
        self.assertEqual(expenses_visible_to(self.owner).count(), 2)


class ExpenseFormVillaTests(TestCase):

    def test_form_offers_every_manageable_villa_and_rejects_foreign_ones(self):
        from .forms import ExpenseForm
        owner = User.objects.create_user('owner2', is_owner=True)
        manager = User.objects.create_user('mgr2')
        business_a = create_business(name='A', created_by=owner)
        business_b = create_business(name='B', created_by=owner)
        villa_a = create_villa(business=business_a, name='Va', created_by=owner)
        villa_b = create_villa(business=business_b, name='Vb', created_by=owner)
        assign_business_manager(user=manager, business=business_a, assigned_by=owner)
        self.assertEqual(set(ExpenseForm(user=owner).fields['villa'].queryset), {villa_a, villa_b})
        self.assertEqual(set(ExpenseForm(user=manager).fields['villa'].queryset), {villa_a})
        data = {'villa': villa_b.pk, 'name': 'Electricity', 'amount': '1', 'date': '2026-03-01'}
        form = ExpenseForm(data, user=manager)
        self.assertFalse(form.is_valid())
        self.assertIn('villa', form.errors)
