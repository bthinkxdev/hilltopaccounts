from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from apps.audit import services as audit_services
from apps.audit.models import Action
from apps.billing.models import Payment
from apps.expenses.models import Expense, PaidBy
from apps.shared.exceptions import DomainError
from .exceptions import HandoverNotSubmitted, InvalidHandoverPayment, SelfConfirmationNotAllowed
from .models import CashHandover

def submit_cash_handover(*, staff, payments, submitted_by, notes='', expenses=()) -> CashHandover:
    """Hand over collected cash, net of the owner-business expenses the staff paid out of it.

    declared = cash collections − staff-paid expenses. Every selected record is locked to this handover
    (until it is rejected), so nothing can be handed over or claimed twice.
    """
    payments = list(payments)
    expenses = list(expenses)
    if not payments:
        raise DomainError('Select at least one cash collection to hand over.')
    live = [CashHandover.Status.SUBMITTED, CashHandover.Status.CONFIRMED]
    for payment in payments:
        if payment.collected_by_id != staff.id:
            raise InvalidHandoverPayment(f'{payment} was not collected by {staff}.')
        if payment.method != Payment.Method.CASH:
            raise InvalidHandoverPayment(f'{payment} is not a cash payment.')
        if payment.is_cancelled:
            raise InvalidHandoverPayment(f'{payment} is cancelled and cannot be handed over.')
        if payment.handovers.filter(status__in=live).exists():
            raise InvalidHandoverPayment(f'{payment} is already part of another handover.')
    for expense in expenses:
        if expense.paid_by != PaidBy.STAFF or expense.paid_by_user_id != staff.id or expense.paid_on is None:
            raise InvalidHandoverPayment(f'{expense} was not paid by {staff} from collected cash.')
        if expense.status != Expense.Status.ACTIVE:
            raise InvalidHandoverPayment(f'{expense} is {expense.status} and cannot be settled.')
        if expense.handovers.filter(status__in=live).exists():
            raise InvalidHandoverPayment(f'{expense} is already part of another handover.')
    collected = sum((p.amount for p in payments), Decimal('0.00'))
    spent = sum((e.amount for e in expenses), Decimal('0.00'))
    declared_amount = collected - spent
    if declared_amount <= 0:
        raise DomainError(f'The expenses you paid (QAR {spent}) are not less than the cash selected (QAR {collected}). Select more collections or fewer expenses.')
    with transaction.atomic():
        handover = CashHandover.objects.create(staff=staff, declared_amount=declared_amount, notes=notes)
        handover.payments.set(payments)
        handover.expenses.set(expenses)
        audit_services.log(user=submitted_by, action=Action.CASH_HANDOVER_SUBMITTED, obj=handover, new_value={'declared_amount': str(declared_amount), 'collected': str(collected), 'expenses_paid': str(spent), 'payment_count': len(payments), 'expense_count': len(expenses)})
    return handover

def confirm_cash_handover(*, handover: CashHandover, confirmed_amount, confirmed_by, notes='') -> CashHandover:
    if handover.status != CashHandover.Status.SUBMITTED:
        raise HandoverNotSubmitted(f'{handover} is not awaiting confirmation.')
    if confirmed_by.pk == handover.staff_id:
        raise SelfConfirmationNotAllowed('A staff member cannot confirm their own handover.')
    with transaction.atomic():
        handover.status = CashHandover.Status.CONFIRMED
        handover.confirmed_amount = confirmed_amount
        handover.confirmed_at = timezone.now()
        handover.confirmed_by = confirmed_by
        if notes:
            handover.notes = notes
        handover.full_clean()
        handover.save()
        from apps.expenses.services import verify_expenses_in_confirmed_handover
        verify_expenses_in_confirmed_handover(handover=handover, confirmed_by=confirmed_by)
        audit_services.log(user=confirmed_by, action=Action.CASH_HANDOVER_CONFIRMED, obj=handover, old_value={'status': CashHandover.Status.SUBMITTED}, new_value={'status': CashHandover.Status.CONFIRMED, 'confirmed_amount': str(confirmed_amount), 'discrepancy': str(handover.declared_amount - confirmed_amount)})
    return handover

def reject_cash_handover(*, handover: CashHandover, rejected_by, reason) -> CashHandover:
    if handover.status != CashHandover.Status.SUBMITTED:
        raise HandoverNotSubmitted(f'{handover} is not awaiting confirmation.')
    with transaction.atomic():
        handover.status = CashHandover.Status.REJECTED
        handover.rejection_reason = reason
        handover.save(update_fields=['status', 'rejection_reason'])
        audit_services.log(user=rejected_by, action=Action.CASH_HANDOVER_REJECTED, obj=handover, old_value={'status': CashHandover.Status.SUBMITTED}, new_value={'status': CashHandover.Status.REJECTED}, reason=reason)
    return handover
