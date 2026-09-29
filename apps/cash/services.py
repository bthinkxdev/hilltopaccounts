from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from apps.audit import services as audit_services
from apps.audit.models import Action
from apps.billing.models import Payment
from apps.shared.exceptions import DomainError
from .exceptions import HandoverNotSubmitted, InvalidHandoverPayment, SelfConfirmationNotAllowed
from .models import CashHandover

def submit_cash_handover(*, staff, payments, submitted_by, notes='') -> CashHandover:
    payments = list(payments)
    if not payments:
        raise DomainError('Select at least one cash collection to hand over.')
    for payment in payments:
        if payment.collected_by_id != staff.id:
            raise InvalidHandoverPayment(f'{payment} was not collected by {staff}.')
        if payment.method != Payment.Method.CASH:
            raise InvalidHandoverPayment(f'{payment} is not a cash payment.')
        if payment.is_cancelled:
            raise InvalidHandoverPayment(f'{payment} is cancelled and cannot be handed over.')
        if payment.handovers.filter(status__in=[CashHandover.Status.SUBMITTED, CashHandover.Status.CONFIRMED]).exists():
            raise InvalidHandoverPayment(f'{payment} is already part of another handover.')
    declared_amount = sum((p.amount for p in payments), Decimal('0.00'))
    with transaction.atomic():
        handover = CashHandover.objects.create(staff=staff, declared_amount=declared_amount, notes=notes)
        handover.payments.set(payments)
        audit_services.log(user=submitted_by, action=Action.CASH_HANDOVER_SUBMITTED, obj=handover, new_value={'declared_amount': str(declared_amount), 'payment_count': len(payments)})
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
