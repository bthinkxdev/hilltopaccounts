import calendar
import re
from datetime import date as date_type, timedelta
from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from apps.accounts import selectors
from apps.audit import services as audit_services
from apps.audit.models import Action
from apps.shared.exceptions import DomainError
from . import assignment
from .models import AssignedTo, Expense, ExpenseCategory, PaidBy, RecurringExpense

class ExpenseNotPayable(DomainError):
    pass

class ExpenseNotEditable(DomainError):
    pass

TRACKED_FIELDS = ['auto_debit_skipped', 'business_id', 'villa_id', 'partition_id', 'category_id', 'amount', 'date', 'due_date', 'paid_on', 'payment_method', 'paid_by', 'assigned_to', 'paid_by_user_id', 'verified_at', 'verified_by_id', 'description', 'reference']
RECURRING_TRACKED_FIELDS = ['villa_id', 'category_id', 'amount', 'due_day', 'paid_by', 'assigned_to', 'auto_debit', 'is_active']

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
    """Auto-debit or landlord rent expenses are settled by the Admin or the villa's Business Manager; other villa expenses by anyone on the villa."""
    if user.is_owner:
        return True
    if expense.recurring_id is not None and getattr(expense.recurring, 'auto_debit', False):
        return selectors.is_business_manager_of(user, expense.business)
    if expense.category_id and re.search(selectors.LANDLORD_RENT_REGEX, expense.category.name, re.I):
        return selectors.is_business_manager_of(user, expense.business)
    if expense.assigned_to == AssignedTo.OWNER:
        return selectors.is_business_manager_of(user, expense.business)  # assigned to the Admin: staff can only view it
    if expense.villa_id:
        return selectors.can_manage_villa(user, expense.villa)
    return selectors.is_business_manager_of(user, expense.business)


def _is_admin(user, business) -> bool:
    """Admin, or the Business Manager of that business — the people who confirm that money was really paid/received."""
    return user.is_owner or selectors.is_business_manager_of(user, business)

def _record_payment_fields(expense: Expense, *, paid_on, payment_method, payer, admin: bool) -> None:
    """Apply a payment to the expense object. An admin paying is final (verified, and owner-paid);
    a staff member paying leaves it awaiting the admin's verification."""
    expense.paid_on = paid_on
    expense.payment_method = payment_method
    expense.paid_by_user = payer
    if admin:
        expense.paid_by = PaidBy.OWNER  # the admin side actually paid it, whoever it was meant for
        expense.verified_at = timezone.now()
        expense.verified_by = payer
    else:
        expense.paid_by = PaidBy.STAFF
        expense.verified_at = None
        expense.verified_by = None

def create_expense(*, villa, category, amount, date, created_by, partition=None, **fields) -> Expense:
    """Record an expense against a villa. The business is always derived from the villa."""
    if villa is None:
        raise ValidationError('An expense must be recorded against a villa.')
    if villa.is_sold:
        raise DomainError(f'Cannot add expenses to sold villa “{villa.name}”.')
    if not selectors.can_manage_villa(created_by, villa):
        raise PermissionDenied('You cannot add expenses to this villa.')
    with transaction.atomic():
        # Nobody picks "paid by": an unpaid expense sits with the side of whoever recorded it, and moves to whoever
        # actually pays it (admin side for the Admin / Business Manager, staff side — from collected cash — otherwise).
        fields.setdefault('paid_by', PaidBy.OWNER if _is_admin(created_by, villa.business) else PaidBy.STAFF)
        # Who is going to pay it is the Admin's call; for anyone else, or when nobody chose, it is decided by the expense itself.
        chosen = assignment.valid(fields.pop('assigned_to', None)) if created_by.is_owner else None
        fields['assigned_to'] = chosen or assignment.default_for(category_name=category.name)
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
            raise PermissionDenied('Only the Admin or Business Manager can settle an expense paid from the admin account.')
        admin = _is_admin(marked_by, locked.business)
        return _settle(locked, paid_on=paid_on, payment_method=payment_method, actor=marked_by, payer=marked_by, admin=admin, reason='marked paid' if admin else 'marked paid by staff — awaiting admin verification')

def _side_before_payment(expense: Expense) -> str:
    """Which side (Admin / Staff) the expense was on before it was paid. Paying overwrites it with whoever paid, so it is
    read back from the payment's own audit entry; an expense that was recorded already paid falls back to the side that
    would have been expected of it."""
    from django.contrib.contenttypes.models import ContentType
    from apps.audit.models import AuditLog
    entries = AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(Expense), object_id=str(expense.pk), action=Action.EXPENSE_UPDATED).order_by('-timestamp', '-pk')
    for entry in entries:
        old, new = entry.old_value or {}, entry.new_value or {}
        if old.get('paid_on') in (None, '', 'None') and new.get('paid_on') not in (None, '', 'None') and old.get('paid_by') in (PaidBy.STAFF, PaidBy.OWNER):
            return old['paid_by']
    if expense.recurring_id is not None:
        return expense.recurring.paid_by
    return PaidBy.OWNER if _is_admin(expense.created_by, expense.business) else PaidBy.STAFF

def revert_expense_payment(*, expense: Expense, reverted_by, reason) -> Expense:
    """Undo a payment that was marked by mistake. Only the payment is undone: the expense goes back to unpaid, on the side
    it was on before (Admin / Staff), with no payer, method, date or verification. Its amount, month, category and every
    other expense are untouched, so no other month changes. Refused when the cash is part of a handover or for a sold
    villa. Reverting an auto-debit payment switches auto-debit off for that one month (it is paid by hand); other months stay automatic. The reason is mandatory and the old and new state is audited."""
    from apps.cash.models import CashHandover
    reason = (reason or '').strip()
    if not reason:
        raise DomainError('A reason is required to revert a payment.')
    with transaction.atomic():
        locked = Expense.objects.select_for_update().select_related('villa', 'business', 'recurring', 'created_by').get(pk=expense.pk)
        if locked.status != Expense.Status.ACTIVE:
            raise ExpenseNotPayable(f'This expense is {locked.status}; there is no payment to revert.')
        if locked.paid_on is None:
            raise ExpenseNotPayable('This expense is not marked paid, so there is no payment to revert.')
        if locked.villa.is_sold:
            raise DomainError(f'Cannot change expenses of sold villa “{locked.villa.name}”.')
        if not selectors.ManageScope(reverted_by).can_revert_expense_payment(locked):
            raise PermissionDenied('Only the Admin can revert a payment.')
        if locked.handovers.filter(status=CashHandover.Status.CONFIRMED).exists():
            raise DomainError('This payment is part of a confirmed cash handover and cannot be reverted directly.')
        if locked.handovers.filter(status=CashHandover.Status.SUBMITTED).exists():
            raise DomainError('This payment is part of a pending cash handover. Reject the handover first.')
        side = _side_before_payment(locked)
        old_value = _snapshot(locked)
        if locked.auto_debit_resettles:
            locked.auto_debit_skipped = True  # auto-debit must not pay this month again straight after the revert; it is paid by hand
        locked.paid_on = None
        locked.payment_method = ''
        locked.paid_by_user = None
        locked.verified_at = None
        locked.verified_by = None
        locked.paid_by = side
        locked.updated_by = reverted_by
        locked.full_clean()
        locked.save(update_fields=['paid_on', 'payment_method', 'paid_by', 'paid_by_user', 'verified_at', 'verified_by', 'auto_debit_skipped', 'updated_by', 'updated_at'])
        audit_services.log(user=reverted_by, action=Action.EXPENSE_PAYMENT_REVERTED, obj=locked, business=locked.business, villa=locked.villa, old_value=old_value, new_value=_snapshot(locked), reason=reason)
    return locked

def verify_expense(*, expense: Expense, verified_by, reason='verified as paid and received') -> Expense:
    """The Admin / Business Manager confirms a staff-paid expense really happened."""
    with transaction.atomic():
        locked = Expense.objects.select_for_update().select_related('villa', 'business').get(pk=expense.pk)
        if not _is_admin(verified_by, locked.business):
            raise PermissionDenied('Only the Admin or the Business Manager can verify an expense.')
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
    """Confirming a handover confirms the expenses the staff netted off it: the admin has accepted those payments.

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
    due = Expense.objects.filter(status=Expense.Status.ACTIVE, paid_on__isnull=True, recurring__auto_debit=True, auto_debit_skipped=False, due_date__lte=today)
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
            if locked.paid_on is not None or locked.status != Expense.Status.ACTIVE or locked.auto_debit_skipped:
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

def revise_unpaid_expense(*, expense: Expense, updated_by, amount, due_date=None, description='', assigned_to=None) -> Expense:
    """Adjust the amount/due date of an expense that has not been paid yet (e.g. this month's electricity bill).

    A paid expense is financial history: it can only be cancelled or reversed, never rewritten.
    """
    with transaction.atomic():
        locked = Expense.objects.select_for_update().select_related('villa', 'business').get(pk=expense.pk)
        if not (selectors.can_manage_villa(updated_by, locked.villa) if locked.villa_id else selectors.is_business_manager_of(updated_by, locked.business)):
            raise PermissionDenied('You cannot edit this expense.')
        if locked.status != Expense.Status.ACTIVE or locked.paid_on is not None:
            raise ExpenseNotEditable('Only an unpaid, active expense can be edited. Cancel or reverse a paid one instead.')
        if due_date is None:
            due_date = locked.due_date
        changes = {'amount': amount, 'due_date': due_date, 'description': description}
        # Who is going to pay it (Admin or Staff) is the Admin's call; an auto-debit is always the Admin's account.
        if assignment.valid(assigned_to) and updated_by.is_owner and not (locked.recurring_id and locked.recurring.auto_debit):
            changes['assigned_to'] = assigned_to
        return update_expense(expense=locked, updated_by=updated_by, **changes)

def _set_status(*, expense: Expense, status, actor, reason) -> Expense:
    with transaction.atomic():
        old_status = expense.status
        expense.status = status
        expense.status_reason = reason
        expense.updated_by = actor
        expense.save(update_fields=['status', 'status_reason', 'updated_by', 'updated_at'])
        audit_services.log(user=actor, action=Action.EXPENSE_CANCELLED, obj=expense, business=expense.business, villa=expense.villa, old_value={'status': old_status}, new_value={'status': status}, reason=reason)
    return expense

def cancel_expense(*, expense: Expense, cancelled_by, reason, forever=False) -> Expense:
    """Take an expense out of the totals. For one month of a monthly expense there are two choices: just that month
    (`forever=False`, the other months carry on) or `forever=True`: that month and every later one, so nothing is shown or
    offered for payment from that month on. Earlier months are never touched."""
    if forever and expense.recurring_id:
        return _cancel_monthly_forever(expense=expense, cancelled_by=cancelled_by, reason=reason)
    return _set_status(expense=expense, status=Expense.Status.CANCELLED, actor=cancelled_by, reason=reason)

def _cancel_monthly_forever(*, expense: Expense, cancelled_by, reason) -> Expense:
    recurring, start = expense.recurring, expense.period
    with transaction.atomic():
        later = list(Expense.objects.select_for_update().filter(recurring=recurring, period__gte=start, status=Expense.Status.ACTIVE).order_by('period'))
        paid = [e for e in later if e.paid_on is not None]
        if paid:
            months = ', '.join(f'{e.period:%B %Y}' for e in paid)
            raise DomainError(f'{recurring.category} is already paid for {months}. Revert that payment first, then cancel.')
        if recurring.is_active:
            deactivate_recurring_expense(recurring=recurring, updated_by=cancelled_by, reason=f'Cancelled from {start:%B %Y} onward: {reason}')
        for e in later:
            _set_status(expense=e, status=Expense.Status.CANCELLED, actor=cancelled_by, reason=reason)
    expense.refresh_from_db()
    return expense

def restore_expense(*, expense: Expense, restored_by, reason) -> Expense:
    """Bring a cancelled (or reversed) expense back as an active, unpaid-as-it-was expense, so it can be edited and paid again.
    Cancelling only takes an expense out of the totals; nothing else about it was changed, so nothing else is touched here.
    A monthly expense's month row is the same row, so that month returns instead of being blocked."""
    reason = (reason or '').strip()
    if not reason:
        raise DomainError('A reason is required to restore an expense.')
    with transaction.atomic():
        locked = Expense.objects.select_for_update().select_related('villa', 'business').get(pk=expense.pk)
        if locked.status == Expense.Status.ACTIVE:
            raise DomainError('This expense is already active.')
        if locked.villa and locked.villa.is_sold:
            raise DomainError(f'Cannot change expenses of sold villa “{locked.villa.name}”.')
        if not selectors.ManageScope(restored_by).can_restore_expense(locked):
            raise PermissionDenied('Only the Admin or Business Manager can restore an expense.')
        old_status = locked.status
        locked.status = Expense.Status.ACTIVE
        locked.status_reason = ''
        locked.updated_by = restored_by
        locked.save(update_fields=['status', 'status_reason', 'updated_by', 'updated_at'])
        audit_services.log(user=restored_by, action=Action.EXPENSE_UPDATED, obj=locked, business=locked.business, villa=locked.villa, old_value={'status': old_status}, new_value={'status': locked.status}, reason=f'Restored: {reason}')
        # A monthly expense that was cancelled "forever" from this month (or an earlier one) runs again from here.
        recurring = locked.recurring if locked.recurring_id else None
        if recurring is not None and not recurring.is_active and locked.period >= date_type.today().replace(day=1):
            update_recurring_expense(recurring=recurring, updated_by=restored_by, is_active=True)
    return locked

# ---- fixed monthly (recurring) expenses ---------------------------------------------------------

def _recurring_snapshot(recurring: RecurringExpense) -> dict:
    return {f: str(getattr(recurring, f)) for f in RECURRING_TRACKED_FIELDS}

def _require_fixed_expense_admin(user, villa):
    if not (user.is_owner or selectors.is_business_manager_of(user, villa.business) or selectors.is_villa_staff_of(user, villa)):
        raise PermissionDenied('Only the Admin, Business Manager, or assigned Villa Staff can set a villa\'s fixed expenses.')

def create_recurring_expense(*, villa, category, amount, due_day, created_by, paid_by=None, assigned_to=None, auto_debit=False, start_period=None) -> RecurringExpense:
    if villa.is_sold:
        raise DomainError(f'Cannot add fixed expenses to sold villa “{villa.name}”.')
    _require_fixed_expense_admin(created_by, villa)
    admin = _is_admin(created_by, villa.business)
    auto_debit = bool(auto_debit) and admin
    if paid_by is None:
        # Who normally pays it follows who sets it up; an auto-debit is always the admin account.
        paid_by = PaidBy.OWNER if (admin or auto_debit) else PaidBy.STAFF
    # Who is going to pay it: the Admin's choice; otherwise the side it was set up for. An auto-debit is always the Admin's account.
    assigned_to = (assignment.valid(assigned_to) if created_by.is_owner else None) or paid_by
    if auto_debit:
        assigned_to = AssignedTo.OWNER
    with transaction.atomic():
        recurring = RecurringExpense(villa=villa, category=category, amount=amount, due_day=due_day, paid_by=paid_by, assigned_to=assigned_to, auto_debit=auto_debit, start_period=start_period.replace(day=1) if start_period else None, created_by=created_by)
        recurring.full_clean()
        recurring.save()
        audit_services.log(user=created_by, action=Action.EXPENSE_CREATED, obj=recurring, business=villa.business, villa=villa, new_value=_recurring_snapshot(recurring), reason='fixed monthly expense set up')
        _generate_months_up_to_now(recurring, created_by)
    return recurring

def _generate_months_up_to_now(recurring: RecurringExpense, actor) -> None:
    """Records every month from the expense's first month up to this one together with the expense itself, so every page
    and total has them at once (a start in a past month is filled in, not left for whichever page is opened first).
    Nothing before the first month, and nothing for a month that has not begun."""
    if not recurring.is_active:
        return
    this_month = date_type.today().replace(day=1)
    period = recurring.first_period
    while period <= this_month:
        _create_fixed_expense(recurring, period, actor)  # None when that month already exists
        period = (period + timedelta(days=32)).replace(day=1)

def _freeze_past_months(recurring: RecurringExpense) -> None:
    """Generate every past month (from its first month up to last month) with the current terms, so a later change
    of amount or payer can only affect this month onwards."""
    from apps.accounts.services import get_system_user
    this_month = date_type.today().replace(day=1)
    have = set(Expense.objects.filter(recurring=recurring).values_list('period', flat=True))
    period, system_user = recurring.first_period, None
    while period < this_month:
        if period not in have:
            system_user = system_user or get_system_user()
            _create_fixed_expense(recurring, period, system_user)
        period = (period + timedelta(days=32)).replace(day=1)

def update_recurring_expense(*, recurring: RecurringExpense, updated_by, **fields) -> RecurringExpense:
    """Changes apply from the current month; past months keep the terms they had."""
    _require_fixed_expense_admin(updated_by, recurring.villa)
    if 'auto_debit' in fields:
        if not _is_admin(updated_by, recurring.villa.business):
            fields.pop('auto_debit')  # only the admin side can debit the admin account
        elif fields['auto_debit']:
            fields['paid_by'] = PaidBy.OWNER
    if fields.get('auto_debit', recurring.auto_debit):
        fields['assigned_to'] = AssignedTo.OWNER  # an auto-debit is always the Admin's account
    elif 'assigned_to' in fields and not (updated_by.is_owner and assignment.valid(fields['assigned_to'])):
        fields.pop('assigned_to')  # only the Admin chooses
    if recurring.is_active and any(f in fields and fields[f] != getattr(recurring, f) for f in ('amount', 'paid_by', 'assigned_to')):
        _freeze_past_months(recurring)
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
        _generate_months_up_to_now(recurring, updated_by)
        if new_value.get('assigned_to') != old_value.get('assigned_to'):
            # From this month on, what is still unpaid follows the new assignment; paid and past months keep theirs.
            Expense.objects.filter(recurring=recurring, status=Expense.Status.ACTIVE, paid_on__isnull=True, period__gte=date_type.today().replace(day=1)).update(assigned_to=recurring.assigned_to)
    return recurring

def deactivate_recurring_expense(*, recurring: RecurringExpense, updated_by, reason='') -> RecurringExpense:
    _require_fixed_expense_admin(updated_by, recurring.villa)
    if recurring.is_active:
        _freeze_past_months(recurring)
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
            expense = Expense(business=villa.business, villa=villa, category=recurring.category, amount=recurring.amount, date=period, due_date=date_type(period.year, period.month, min(recurring.due_day, last_day)), paid_by=recurring.paid_by, assigned_to=recurring.assigned_to, recurring=recurring, period=period, created_by=generated_by)
            expense.full_clean(validate_constraints=False)
            expense.save()
            audit_services.log(user=generated_by, action=Action.EXPENSE_CREATED, obj=expense, business=villa.business, villa=villa, new_value=_snapshot(expense), reason=f'fixed expense for {period:%B %Y}')
    except IntegrityError:
        return None  # another request created it first
    return expense

MAX_PAY_AHEAD_MONTHS = 12

def pay_fixed_expense_ahead(*, recurring: RecurringExpense, year, month, paid_on, payment_method, marked_by, amount=None) -> Expense:
    """Pay a monthly repeated expense before its month starts (e.g. next quarter's villa rent).

    Normally a month's record is only created when the month begins. Here it is created now, exactly as it would be then
    (same fixed amount, due date and month), and paid in the same transaction: either both happen or neither. The expense
    stays in its own month for every total; only the payment itself is dated today. `amount` overrides the scheduled amount
    for that month only (recorded in the audit log as an update); the monthly expense keeps its own amount.
    """
    period = date_type(year, month, 1)
    this_month = date_type.today().replace(day=1)
    if not recurring.is_active:
        raise DomainError(f'“{recurring.category}” is no longer a monthly expense of this villa.')
    if recurring.villa.is_sold:
        raise DomainError(f'Cannot pay expenses of sold villa “{recurring.villa.name}”.')
    if period < recurring.first_period:
        raise DomainError(f'“{recurring.category}” starts in {recurring.first_period:%B %Y}; earlier months do not apply.')
    months_ahead = (period.year - this_month.year) * 12 + period.month - this_month.month
    if months_ahead > MAX_PAY_AHEAD_MONTHS:
        raise DomainError(f'Expenses can be paid at most {MAX_PAY_AHEAD_MONTHS} months ahead.')
    with transaction.atomic():
        expense = Expense.objects.filter(recurring=recurring, period=period).first() or _create_fixed_expense(recurring, period, marked_by)
        if expense is None:  # another request created it first
            expense = Expense.objects.get(recurring=recurring, period=period)
        if amount is not None and Decimal(str(amount)) != expense.amount:
            if Decimal(str(amount)) <= 0:
                raise DomainError('The amount must be greater than zero.')
            expense = update_expense(expense=expense, updated_by=marked_by, amount=Decimal(str(amount)))
        return mark_expense_paid(expense=expense, paid_on=paid_on, payment_method=payment_method, marked_by=marked_by)

def _generate_for_period(*, villa, year, month, generated_by) -> list[Expense]:
    period = date_type(year, month, 1)
    templates = [t for t in RecurringExpense.objects.filter(villa=villa, is_active=True).select_related('category', 'villa', 'villa__business') if t.first_period <= period]
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
    templates = [t for t in RecurringExpense.objects.filter(villa_id__in=villa_ids, is_active=True).select_related('category', 'villa', 'villa__business') if t.first_period <= period]
    created = 0
    if templates:
        have = set(Expense.objects.filter(recurring_id__in=[t.pk for t in templates], period=period).values_list('recurring_id', flat=True))
        missing = [t for t in templates if t.pk not in have]
        if missing:
            system_user = get_system_user()
            created = sum(1 for t in missing if _create_fixed_expense(t, period, system_user) is not None)
    settle_auto_debits(today=today, villa_ids=villa_ids)
    return created
