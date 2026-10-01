import calendar
from datetime import date as date_type
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from apps.accounts import selectors
from apps.audit import services as audit_services
from apps.audit.models import Action
from apps.shared.exceptions import DomainError
from .models import Expense, ExpenseCategory, PaidBy, RecurringExpense

class ExpenseNotPayable(DomainError):
    pass

class ExpenseNotEditable(DomainError):
    pass

TRACKED_FIELDS = ['business_id', 'villa_id', 'partition_id', 'category_id', 'amount', 'date', 'due_date', 'paid_on', 'payment_method', 'paid_by', 'paid_by_user_id', 'description', 'reference']
RECURRING_TRACKED_FIELDS = ['villa_id', 'category_id', 'amount', 'due_day', 'paid_by', 'auto_debit', 'is_active']

def get_or_create_category(*, name) -> ExpenseCategory:
    """Expense names are a shared catalogue; match case-insensitively so 'electricity' reuses 'Electricity'."""
    name = ' '.join(name.split())
    existing = ExpenseCategory.objects.filter(name__iexact=name).first()
    if existing is not None:
        return existing
    return ExpenseCategory.objects.create(name=name)

def _snapshot(expense: Expense) -> dict:
    return {f: str(getattr(expense, f)) for f in TRACKED_FIELDS}

def can_pay_expense(user, expense: Expense) -> bool:
    """Owner-account expenses are settled by the Owner or the villa's Business Manager; staff-paid ones by anyone on the villa."""
    if user.is_owner:
        return True
    if expense.paid_by == PaidBy.OWNER:
        return selectors.is_business_manager_of(user, expense.business)
    if expense.villa_id:
        return selectors.can_manage_villa(user, expense.villa)
    return selectors.is_business_manager_of(user, expense.business)

def create_expense(*, villa, category, amount, date, created_by, partition=None, **fields) -> Expense:
    """Record an expense against a villa. The business is always derived from the villa."""
    if villa is None:
        raise ValidationError('An expense must be recorded against a villa.')
    if not selectors.can_manage_villa(created_by, villa):
        raise PermissionDenied('You cannot add expenses to this villa.')
    with transaction.atomic():
        expense = Expense(business=villa.business, villa=villa, partition=partition, category=category, amount=amount, date=date, created_by=created_by, **fields)
        if expense.paid_on is not None:
            if not can_pay_expense(created_by, expense):
                raise PermissionDenied('You cannot record a payment for an owner-account expense.')
            expense.paid_by_user = created_by
        expense.full_clean()
        expense.save()
        audit_services.log(user=created_by, action=Action.EXPENSE_CREATED, obj=expense, business=villa.business, villa=villa, new_value=_snapshot(expense))
    return expense

def _settle(locked: Expense, *, paid_on, payment_method, actor, paid_by_user, reason) -> Expense:
    if locked.status != Expense.Status.ACTIVE:
        raise ExpenseNotPayable(f'This expense is {locked.status} and cannot be marked paid.')
    if locked.paid_on is not None:
        raise ExpenseNotPayable('This expense is already marked paid.')
    old_value = _snapshot(locked)
    locked.paid_on = paid_on
    locked.payment_method = payment_method
    locked.paid_by_user = paid_by_user
    locked.updated_by = actor
    locked.full_clean()
    locked.save(update_fields=['paid_on', 'payment_method', 'paid_by_user', 'updated_by', 'updated_at'])
    audit_services.log(user=actor, action=Action.EXPENSE_UPDATED, obj=locked, business=locked.business, villa=locked.villa, old_value=old_value, new_value=_snapshot(locked), reason=reason)
    return locked

def mark_expense_paid(*, expense: Expense, paid_on, payment_method, marked_by) -> Expense:
    with transaction.atomic():
        locked = Expense.objects.select_for_update().select_related('villa', 'business').get(pk=expense.pk)
        if not can_pay_expense(marked_by, locked):
            raise PermissionDenied('Only the Owner or Business Manager can settle an expense paid from the owner account.')
        return _settle(locked, paid_on=paid_on, payment_method=payment_method, actor=marked_by, paid_by_user=marked_by, reason='marked paid')

def settle_auto_debits(*, today=None, villa=None) -> int:
    """Mark fixed expenses flagged auto-debit as paid once their due date has arrived. Returns how many were settled."""
    from apps.accounts.services import get_system_user
    today = today or date_type.today()
    system_user = get_system_user()
    due = Expense.objects.filter(status=Expense.Status.ACTIVE, paid_on__isnull=True, recurring__auto_debit=True, due_date__lte=today)
    if villa is not None:
        due = due.filter(villa=villa)
    settled = 0
    for pk in list(due.values_list('pk', flat=True)):
        with transaction.atomic():
            locked = Expense.objects.select_for_update().select_related('villa', 'business').get(pk=pk)
            if locked.paid_on is not None or locked.status != Expense.Status.ACTIVE:
                continue
            _settle(locked, paid_on=locked.due_date, payment_method=Expense.Method.BANK_TRANSFER, actor=system_user, paid_by_user=system_user, reason='auto-debit from owner account')
            settled += 1
    return settled

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

def revise_unpaid_expense(*, expense: Expense, updated_by, amount, due_date, description='') -> Expense:
    """Adjust the amount/due date of an expense that has not been paid yet (e.g. this month's electricity bill).

    A paid expense is financial history: it can only be cancelled or reversed, never rewritten.
    """
    with transaction.atomic():
        locked = Expense.objects.select_for_update().select_related('villa', 'business').get(pk=expense.pk)
        if not (selectors.can_manage_villa(updated_by, locked.villa) if locked.villa_id else selectors.is_business_manager_of(updated_by, locked.business)):
            raise PermissionDenied('You cannot edit this expense.')
        if locked.status != Expense.Status.ACTIVE or locked.paid_on is not None:
            raise ExpenseNotEditable('Only an unpaid, active expense can be edited. Cancel or reverse a paid one instead.')
        return update_expense(expense=locked, updated_by=updated_by, amount=amount, due_date=due_date, description=description)

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

# ---- fixed monthly (recurring) expenses ---------------------------------------------------------

def _recurring_snapshot(recurring: RecurringExpense) -> dict:
    return {f: str(getattr(recurring, f)) for f in RECURRING_TRACKED_FIELDS}

def _require_fixed_expense_admin(user, villa):
    if not (user.is_owner or selectors.is_business_manager_of(user, villa.business)):
        raise PermissionDenied('Only the Owner or the Business Manager can set a villa\'s fixed expenses.')

def create_recurring_expense(*, villa, category, amount, due_day, created_by, paid_by=PaidBy.OWNER, auto_debit=False) -> RecurringExpense:
    _require_fixed_expense_admin(created_by, villa)
    with transaction.atomic():
        recurring = RecurringExpense(villa=villa, category=category, amount=amount, due_day=due_day, paid_by=paid_by, auto_debit=auto_debit, created_by=created_by)
        recurring.full_clean()
        recurring.save()
        audit_services.log(user=created_by, action=Action.EXPENSE_CREATED, obj=recurring, business=villa.business, villa=villa, new_value=_recurring_snapshot(recurring), reason='fixed monthly expense set up')
    return recurring

def update_recurring_expense(*, recurring: RecurringExpense, updated_by, **fields) -> RecurringExpense:
    """Changes apply from the next generated month; already-generated expenses are left as they were."""
    _require_fixed_expense_admin(updated_by, recurring.villa)
    old_value = _recurring_snapshot(recurring)
    for field, value in fields.items():
        setattr(recurring, field, value)
    recurring.updated_by = updated_by
    with transaction.atomic():
        recurring.full_clean()
        recurring.save()
        new_value = _recurring_snapshot(recurring)
        if new_value != old_value:
            audit_services.log(user=updated_by, action=Action.EXPENSE_UPDATED, obj=recurring, business=recurring.villa.business, villa=recurring.villa, old_value=old_value, new_value=new_value, reason='fixed monthly expense changed')
    return recurring

def deactivate_recurring_expense(*, recurring: RecurringExpense, updated_by, reason='') -> RecurringExpense:
    _require_fixed_expense_admin(updated_by, recurring.villa)
    with transaction.atomic():
        recurring.is_active = False
        recurring.updated_by = updated_by
        recurring.save(update_fields=['is_active', 'updated_by', 'updated_at'])
        audit_services.log(user=updated_by, action=Action.EXPENSE_CANCELLED, obj=recurring, business=recurring.villa.business, villa=recurring.villa, old_value={'is_active': True}, new_value={'is_active': False}, reason=reason or 'fixed monthly expense stopped')
    return recurring

def generate_recurring_expenses(*, villa, year, month, generated_by) -> list[Expense]:
    """Create this month's expense for each active fixed expense of the villa. Safe to run repeatedly and concurrently."""
    if not selectors.can_manage_villa(generated_by, villa):
        raise PermissionDenied('You cannot generate expenses for this villa.')
    return _generate_for_period(villa=villa, year=year, month=month, generated_by=generated_by)

def generate_recurring_expenses_as_system(*, villa, year, month) -> list[Expense]:
    """Scheduled run (management command): attributed to the system user in the audit log."""
    from apps.accounts.services import get_system_user
    return _generate_for_period(villa=villa, year=year, month=month, generated_by=get_system_user())

def _generate_for_period(*, villa, year, month, generated_by) -> list[Expense]:
    period = date_type(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    created = []
    templates = RecurringExpense.objects.filter(villa=villa, is_active=True).select_related('category')
    already = set(Expense.objects.filter(recurring__in=templates, period=period).values_list('recurring_id', flat=True))
    for recurring in templates:
        if recurring.pk in already:
            continue
        try:
            with transaction.atomic():
                expense = Expense(business=villa.business, villa=villa, category=recurring.category, amount=recurring.amount, date=period, due_date=date_type(year, month, min(recurring.due_day, last_day)), paid_by=recurring.paid_by, recurring=recurring, period=period, created_by=generated_by)
                expense.full_clean(validate_constraints=False)  # the database constraint is the arbiter of 'once per month'
                expense.save()
                audit_services.log(user=generated_by, action=Action.EXPENSE_CREATED, obj=expense, business=villa.business, villa=villa, new_value=_snapshot(expense), reason=f'fixed expense for {period:%B %Y}')
        except IntegrityError:
            continue  # another request generated it first; the unique constraint kept it to one
        created.append(expense)
    return created
