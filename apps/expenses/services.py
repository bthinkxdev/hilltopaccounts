from django.db import transaction
from apps.audit import services as audit_services
from apps.audit.models import Action
from .models import Expense, ExpenseCategory
TRACKED_FIELDS = ['business_id', 'villa_id', 'partition_id', 'category_id', 'amount', 'date', 'payment_method', 'description', 'reference']

def get_or_create_category(*, name) -> ExpenseCategory:
    category, _ = ExpenseCategory.objects.get_or_create(name=name)
    return category

def _snapshot(expense: Expense) -> dict:
    return {f: str(getattr(expense, f)) for f in TRACKED_FIELDS}

def create_expense(*, business, category, amount, date, payment_method, created_by, villa=None, partition=None, **fields) -> Expense:
    with transaction.atomic():
        expense = Expense(business=business, villa=villa, partition=partition, category=category, amount=amount, date=date, payment_method=payment_method, created_by=created_by, **fields)
        expense.full_clean()
        expense.save()
        audit_services.log(user=created_by, action=Action.EXPENSE_CREATED, obj=expense, business=business, villa=villa, new_value=_snapshot(expense))
    return expense

def update_expense(*, expense: Expense, updated_by, **fields) -> Expense:
    old_value = _snapshot(expense)
    for field, value in fields.items():
        setattr(expense, field, value)
    expense.updated_by = updated_by
    with transaction.atomic():
        expense.full_clean()
        expense.save()
        new_value = _snapshot(expense)
        if new_value != old_value:
            audit_services.log(user=updated_by, action=Action.EXPENSE_UPDATED, obj=expense, business=expense.business, villa=expense.villa, old_value=old_value, new_value=new_value)
    return expense

def _set_status(*, expense: Expense, status, actor, reason) -> Expense:
    with transaction.atomic():
        old_status = expense.status
        expense.status = status
        expense.status_reason = reason
        expense.updated_by = actor
        expense.save(update_fields=['status', 'status_reason', 'updated_by', 'updated_at'])
        audit_services.log(user=actor, action=Action.EXPENSE_CANCELLED, obj=expense, business=expense.business, villa=expense.villa, old_value={'status': old_status}, new_value={'status': status}, reason=reason)
    return expense

def cancel_expense(*, expense: Expense, cancelled_by, reason) -> Expense:
    return _set_status(expense=expense, status=Expense.Status.CANCELLED, actor=cancelled_by, reason=reason)

def reverse_expense(*, expense: Expense, reversed_by, reason) -> Expense:
    return _set_status(expense=expense, status=Expense.Status.REVERSED, actor=reversed_by, reason=reason)
