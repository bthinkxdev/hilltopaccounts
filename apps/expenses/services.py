import calendar
from datetime import date as date_type
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from apps.accounts import selectors
from apps.audit import services as audit_services
from apps.audit.models import Action
from apps.shared.exceptions import DomainError
from .models import Expense, ExpenseCategory, PaidBy, RecurringExpense

class ExpenseNotPayable(DomainError):
    pass

class ExpenseNotEditable(DomainError):
    pass

TRACKED_FIELDS = ['business_id', 'villa_id', 'partition_id', 'category_id', 'amount', 'date', 'due_date', 'paid_on', 'payment_method', 'paid_by', 'paid_by_user_id', 'verified_at', 'verified_by_id', 'description', 'reference']
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

def _is_admin(user, business) -> bool:
    """Owner, or the Business Manager of that business — the people who confirm that money was really paid/received."""
    return user.is_owner or selectors.is_business_manager_of(user, business)

def _record_payment_fields(expense: Expense, *, paid_on, payment_method, payer, admin: bool) -> None:
    """Apply a payment to the expense object. An admin paying is final (verified, and owner-paid);
    a staff member paying leaves it awaiting the owner's verification."""
    expense.paid_on = paid_on
    expense.payment_method = payment_method
    expense.paid_by_user = payer
    if admin:
        expense.paid_by = PaidBy.OWNER  # the owner side actually paid it, whoever it was meant for
        expense.verified_at = timezone.now()
        expense.verified_by = payer
    else:
        expense.verified_at = None
        expense.verified_by = None

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
            _record_payment_fields(expense, paid_on=expense.paid_on, payment_method=expense.payment_method, payer=created_by, admin=_is_admin(created_by, villa.business))
        expense.full_clean()
        expense.save()
        audit_services.log(user=created_by, action=Action.EXPENSE_CREATED, obj=expense, business=villa.business, villa=villa, new_value=_snapshot(expense))
    return expense

def _settle(locked: Expense, *, paid_on, payment_method, actor, payer, admin: bool, reason) -> Expense:
    if locked.status != Expense.Status.ACTIVE:
        raise ExpenseNotPayable(f'This expense is {locked.status} and cannot be marked paid.')
    if locked.paid_on is not None:
        raise ExpenseNotPayable('This expense is already marked paid.')
    old_value = _snapshot(locked)
    _record_payment_fields(locked, paid_on=paid_on, payment_method=payment_method, payer=payer, admin=admin)
    locked.updated_by = actor
    locked.full_clean()
    locked.save(update_fields=['paid_on', 'payment_method', 'paid_by', 'paid_by_user', 'verified_at', 'verified_by', 'updated_by', 'updated_at'])
    audit_services.log(user=actor, action=Action.EXPENSE_UPDATED, obj=locked, business=locked.business, villa=locked.villa, old_value=old_value, new_value=_snapshot(locked), reason=reason)
    return locked

def mark_expense_paid(*, expense: Expense, paid_on, payment_method, marked_by) -> Expense:
    with transaction.atomic():
        locked = Expense.objects.select_for_update().select_related('villa', 'business').get(pk=expense.pk)
        if not can_pay_expense(marked_by, locked):
            raise PermissionDenied('Only the Owner or Business Manager can settle an expense paid from the owner account.')
        admin = _is_admin(marked_by, locked.business)
        return _settle(locked, paid_on=paid_on, payment_method=payment_method, actor=marked_by, payer=marked_by, admin=admin, reason='marked paid' if admin else 'marked paid by staff — awaiting owner verification')

def verify_expense(*, expense: Expense, verified_by, reason='verified as paid and received') -> Expense:
    """The Owner / Business Manager confirms a staff-paid expense really happened."""
    with transaction.atomic():
        locked = Expense.objects.select_for_update().select_related('villa', 'business').get(pk=expense.pk)
        if not _is_admin(verified_by, locked.business):
            raise PermissionDenied('Only the Owner or the Business Manager can verify an expense.')
        if locked.status != Expense.Status.ACTIVE or locked.paid_on is None:
            raise ExpenseNotPayable('Only an active, paid expense can be verified.')
        if locked.verified_at is not None:
            raise ExpenseNotPayable('This expense is already verified.')
        old_value = _snapshot(locked)
        locked.verified_at = timezone.now()
        locked.verified_by = verified_by
        locked.updated_by = verified_by
        locked.save(update_fields=['verified_at', 'verified_by', 'updated_by', 'updated_at'])
        audit_services.log(user=verified_by, action=Action.EXPENSE_UPDATED, obj=locked, business=locked.business, villa=locked.villa, old_value=old_value, new_value=_snapshot(locked), reason=reason)
    return locked

def verify_expenses_in_confirmed_handover(*, handover, confirmed_by) -> int:
    """Confirming a handover confirms the expenses the staff netted off it: the owner has accepted those payments.

    Called only from cash.services.confirm_cash_handover, which has already authorised the confirmer.
    """
    verified = 0
    for expense in handover.expenses.select_for_update().select_related('villa', 'business').filter(status=Expense.Status.ACTIVE, paid_on__isnull=False, verified_at__isnull=True):
        old_value = _snapshot(expense)
        expense.verified_at = timezone.now()
        expense.verified_by = confirmed_by
        expense.updated_by = confirmed_by
        expense.save(update_fields=['verified_at', 'verified_by', 'updated_by', 'updated_at'])
        audit_services.log(user=confirmed_by, action=Action.EXPENSE_UPDATED, obj=expense, business=expense.business, villa=expense.villa, old_value=old_value, new_value=_snapshot(expense), reason=f'verified by confirming cash handover #{handover.pk}')
        verified += 1
    return verified

def settle_auto_debits(*, today=None, villa=None, villa_ids=None) -> int:
    """Mark fixed expenses flagged auto-debit as paid once their due date has arrived. Returns how many were settled."""
    from apps.accounts.services import get_system_user
    today = today or date_type.today()
    due = Expense.objects.filter(status=Expense.Status.ACTIVE, paid_on__isnull=True, recurring__auto_debit=True, due_date__lte=today)
    if villa is not None:
        due = due.filter(villa=villa)
    if villa_ids is not None:
        due = due.filter(villa_id__in=villa_ids)
    pks = list(due.values_list('pk', flat=True))
    if not pks:
        return 0
    system_user = get_system_user()
    settled = 0
    for pk in pks:
        with transaction.atomic():
            locked = Expense.objects.select_for_update().select_related('villa', 'business').get(pk=pk)
            if locked.paid_on is not None or locked.status != Expense.Status.ACTIVE:
                continue
            _settle(locked, paid_on=locked.due_date, payment_method=Expense.Method.BANK_TRANSFER, actor=system_user, payer=system_user, admin=True, reason='auto-debit from owner account')
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

def _create_fixed_expense(recurring: RecurringExpense, period, generated_by):
    """One month's expense for one fixed expense; None when it already exists (the unique constraint is the arbiter)."""
    villa = recurring.villa
    last_day = calendar.monthrange(period.year, period.month)[1]
    try:
        with transaction.atomic():
            expense = Expense(business=villa.business, villa=villa, category=recurring.category, amount=recurring.amount, date=period, due_date=date_type(period.year, period.month, min(recurring.due_day, last_day)), paid_by=recurring.paid_by, recurring=recurring, period=period, created_by=generated_by)
            expense.full_clean(validate_constraints=False)
            expense.save()
            audit_services.log(user=generated_by, action=Action.EXPENSE_CREATED, obj=expense, business=villa.business, villa=villa, new_value=_snapshot(expense), reason=f'fixed expense for {period:%B %Y}')
    except IntegrityError:
        return None  # another request created it first
    return expense

def _generate_for_period(*, villa, year, month, generated_by) -> list[Expense]:
    period = date_type(year, month, 1)
    templates = list(RecurringExpense.objects.filter(villa=villa, is_active=True).select_related('category', 'villa', 'villa__business'))
    already = set(Expense.objects.filter(recurring_id__in=[t.pk for t in templates], period=period).values_list('recurring_id', flat=True))
    created = [expense for recurring in templates if recurring.pk not in already for expense in [_create_fixed_expense(recurring, period, generated_by)] if expense is not None]
    return created

def ensure_fixed_expenses(*, villa_ids, year, month) -> int:
    """Make every month show its fixed expenses by itself: create what is missing (up to the current month) and
    apply auto-debits that have fallen due.

    Idempotent and attributed to the system user, exactly like the scheduled command, so nobody has to
    'add the rent again every month'. Constant queries when nothing is missing, however many villas.
    """
    from apps.accounts.services import get_system_user
    today = date_type.today()
    villa_ids = list(villa_ids)
    if not villa_ids or (year, month) > (today.year, today.month):
        return 0
    period = date_type(year, month, 1)
    templates = list(RecurringExpense.objects.filter(villa_id__in=villa_ids, is_active=True).select_related('category', 'villa', 'villa__business'))
    created = 0
    if templates:
        have = set(Expense.objects.filter(recurring_id__in=[t.pk for t in templates], period=period).values_list('recurring_id', flat=True))
        missing = [t for t in templates if t.pk not in have]
        if missing:
            system_user = get_system_user()
            created = sum(1 for t in missing if _create_fixed_expense(t, period, system_user) is not None)
    settle_auto_debits(today=today, villa_ids=villa_ids)
    return created
