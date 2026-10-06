from datetime import date, timedelta
from django.core.exceptions import PermissionDenied
from django.db import transaction
from apps.accounts import selectors as access
from apps.audit import services as audit_services
from apps.audit.models import Action
from . import selectors
from .exceptions import DuplicateInvoicePeriod, InvoiceCancelled, InvoiceHasPayments, NothingToBill, NothingToCollect, PartitionVacant, PaymentExceedsOutstanding, PaymentInHandover
from .models import Charge, ChargeType, Invoice, InvoiceItem, Payment
CHARGE_TRACKED_FIELDS = ['charge_type_id', 'description', 'amount', 'frequency', 'start_date', 'end_date', 'is_active']

def get_or_create_charge_type(*, name) -> ChargeType:
    charge_type, _ = ChargeType.objects.get_or_create(name=name)
    return charge_type

def ensure_rent_charge(*, partition, tenant, created_by):
    """Create the tenant's monthly Rent charge unless the partition already has an active one."""
    rent_type = get_or_create_charge_type(name='Rent')
    if Charge.objects.filter(partition=partition, charge_type=rent_type, is_active=True).exists():
        return None
    return create_charge(partition=partition, charge_type=rent_type, amount=tenant.monthly_rent, start_date=tenant.move_in_date, created_by=created_by, frequency=Charge.Frequency.MONTHLY)

def close_future_charges(*, partition, move_out_date, closed_by):
    """Move-out: end running monthly charges at the move-out date and deactivate anything starting after it."""
    for charge in Charge.objects.filter(partition=partition, is_active=True):
        if charge.start_date > move_out_date:
            cancel_charge(charge=charge, cancelled_by=closed_by, reason='Tenant moved out')
        elif charge.frequency == Charge.Frequency.MONTHLY and (charge.end_date is None or charge.end_date > move_out_date):
            update_charge(charge=charge, updated_by=closed_by, end_date=move_out_date)

def _charge_snapshot(charge: Charge) -> dict:
    return {f: str(getattr(charge, f)) for f in CHARGE_TRACKED_FIELDS}

def create_charge(*, partition, charge_type, amount, start_date, created_by, **fields) -> Charge:
    with transaction.atomic():
        charge = Charge(partition=partition, charge_type=charge_type, amount=amount, start_date=start_date, created_by=created_by, **fields)
        charge.full_clean()
        charge.save()
        audit_services.log(user=created_by, action=Action.CHARGE_CREATED, obj=charge, business=partition.villa.business, villa=partition.villa, new_value=_charge_snapshot(charge))
    return charge

def update_charge(*, charge: Charge, updated_by, **fields) -> Charge:
    old_value = _charge_snapshot(charge)
    for field, value in fields.items():
        setattr(charge, field, value)
    charge.updated_by = updated_by
    with transaction.atomic():
        charge.full_clean()
        charge.save()
        new_value = _charge_snapshot(charge)
        if new_value != old_value:
            audit_services.log(user=updated_by, action=Action.CHARGE_UPDATED, obj=charge, business=charge.partition.villa.business, villa=charge.partition.villa, old_value=old_value, new_value=new_value)
    return charge

def cancel_charge(*, charge: Charge, cancelled_by, reason='') -> Charge:
    with transaction.atomic():
        charge.is_active = False
        charge.updated_by = cancelled_by
        charge.save(update_fields=['is_active', 'updated_by', 'updated_at'])
        audit_services.log(user=cancelled_by, action=Action.CHARGE_CANCELLED, obj=charge, business=charge.partition.villa.business, villa=charge.partition.villa, old_value={'is_active': True}, new_value={'is_active': False}, reason=reason)
    return charge

def generate_monthly_invoice(*, partition, billing_period_start, billing_period_end, issue_date, due_date, generated_by, notes='') -> Invoice:
    tenant = partition.current_tenant
    if tenant is None:
        raise PartitionVacant(f'{partition} has no active tenant — nothing to bill.')
    if Invoice.objects.filter(partition=partition, billing_period_start=billing_period_start, billing_period_end=billing_period_end).exists() or Invoice.objects.filter(partition=partition, is_cancelled=False, billing_period_start__lte=billing_period_end, billing_period_end__gte=billing_period_start).exists():
        raise DuplicateInvoicePeriod(f'{partition} already has an invoice for {billing_period_start}–{billing_period_end}.')
    active_charges = Charge.objects.filter(partition=partition, is_active=True, start_date__lte=billing_period_end).exclude(end_date__lt=billing_period_start)
    # A charge already billed on a live (non-cancelled) invoice must not be billed again:
    # one-time charges are billed once ever; monthly charges once per overlapping period.
    live_items = InvoiceItem.objects.filter(invoice__is_cancelled=False)
    active_charges = active_charges.exclude(frequency=Charge.Frequency.ONE_TIME, invoice_items__in=live_items)
    overlapping_items = live_items.filter(invoice__billing_period_start__lte=billing_period_end, invoice__billing_period_end__gte=billing_period_start)
    active_charges = active_charges.exclude(invoice_items__in=overlapping_items).distinct()
    charges = list(active_charges.select_related('charge_type'))
    if not any(charge.amount > 0 for charge in charges):
        raise NothingToBill(f'{partition} has no billable charge for {billing_period_start}–{billing_period_end}. Add or activate a rent charge first.')
    with transaction.atomic():
        invoice = Invoice(partition=partition, tenant=tenant, billing_period_start=billing_period_start, billing_period_end=billing_period_end, issue_date=issue_date, due_date=due_date, notes=notes, created_by=generated_by)
        invoice.full_clean()
        invoice.save()
        for charge in charges:
            InvoiceItem.objects.create(invoice=invoice, charge=charge, description=charge.description or charge.charge_type.name, amount=charge.amount)
        audit_services.log(user=generated_by, action=Action.INVOICE_CREATED, obj=invoice, business=partition.villa.business, villa=partition.villa, new_value={'invoice_number': invoice.invoice_number, 'tenant': tenant.name, 'total': str(selectors.invoice_total(invoice)), 'line_items': len(charges)})
    return invoice

def cancel_invoice(*, invoice: Invoice, cancelled_by, reason='') -> Invoice:
    if invoice.is_cancelled:
        raise InvoiceCancelled(f'{invoice} is already cancelled.')
    if invoice.payments.filter(is_cancelled=False).exists():
        raise InvoiceHasPayments(f'{invoice} has recorded payments and cannot be cancelled. Reverse the payments first.')
    with transaction.atomic():
        invoice.is_cancelled = True
        invoice.updated_by = cancelled_by
        invoice.save(update_fields=['is_cancelled', 'updated_by', 'updated_at'])
        audit_services.log(user=cancelled_by, action=Action.INVOICE_CANCELLED, obj=invoice, business=invoice.business, villa=invoice.villa, old_value={'is_cancelled': False}, new_value={'is_cancelled': True}, reason=reason)
    return invoice

def record_payment(*, invoice: Invoice, amount, method, collected_by, collected_at, created_by, reference='', notes='') -> Payment:
    with transaction.atomic():
        locked_invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if locked_invoice.is_cancelled:
            raise InvoiceCancelled(f'{locked_invoice} is cancelled — cannot record a payment against it.')
        outstanding = selectors.invoice_outstanding(locked_invoice)
        if amount > outstanding:
            raise PaymentExceedsOutstanding(f'Payment {amount} exceeds outstanding balance {outstanding} on {locked_invoice}.')
        payment = Payment(invoice=locked_invoice, amount=amount, method=method, collected_by=collected_by, collected_at=collected_at, created_by=created_by, reference=reference, notes=notes)
        payment.full_clean()
        payment.save()
        audit_services.log(user=created_by, action=Action.COLLECTION_CREATED, obj=payment, business=locked_invoice.business, villa=locked_invoice.villa, new_value={'amount': str(amount), 'method': method, 'collected_by': collected_by.username, 'invoice': locked_invoice.invoice_number})
        from apps.notifications.services import notify_collection_recorded
        notify_collection_recorded(payment)
    return payment

def cancel_payment(*, payment: Payment, cancelled_by, reason) -> Payment:
    from apps.cash.models import CashHandover
    if payment.handovers.filter(status=CashHandover.Status.CONFIRMED).exists():
        raise PaymentInHandover(f'{payment} is part of a confirmed cash handover and cannot be cancelled directly. Use a controlled reversal.')
    if payment.handovers.filter(status=CashHandover.Status.SUBMITTED).exists():
        raise PaymentInHandover(f'{payment} is part of a pending cash handover. Reject the handover first.')
    with transaction.atomic():
        payment.is_cancelled = True
        payment.cancelled_reason = reason
        payment.save(update_fields=['is_cancelled', 'cancelled_reason'])
        audit_services.log(user=cancelled_by, action=Action.COLLECTION_CANCELLED, obj=payment, business=payment.invoice.business, villa=payment.invoice.villa, old_value={'is_cancelled': False}, new_value={'is_cancelled': True}, reason=reason)
    return payment

def correct_payment(*, payment: Payment, new_amount, corrected_by, reason, method=None, collected_at=None) -> Payment:
    with transaction.atomic():
        cancel_payment(payment=payment, cancelled_by=corrected_by, reason=f'corrected: {reason}')
        new_payment = record_payment(invoice=payment.invoice, amount=new_amount, method=method or payment.method, collected_by=payment.collected_by, collected_at=collected_at or payment.collected_at, created_by=corrected_by, reference=payment.reference, notes=payment.notes)
        new_payment.corrects = payment
        new_payment.save(update_fields=['corrects'])
        audit_services.log(user=corrected_by, action=Action.COLLECTION_UPDATED, obj=new_payment, business=new_payment.invoice.business, villa=new_payment.invoice.villa, old_value={'amount': str(payment.amount)}, new_value={'amount': str(new_amount)}, reason=reason)
    return new_payment


def collect_rent(*, partition, year, month, method, collected_by) -> Payment:
    """One-tap monthly rent collection for a partition.

    Creates the month's invoice on first use (and the rent charge from the tenant's monthly rent when the
    partition has no charges yet), then records a payment for the full balance. The partition row is locked,
    so a double-tap or two staff acting together can never bill or collect the month twice.
    """
    from apps.villas.models import Partition
    if not access.can_manage_villa(collected_by, partition.villa):
        raise PermissionDenied('You cannot collect rent for this villa.')
    start, end = selectors.period_bounds(year, month)
    with transaction.atomic():
        locked = Partition.objects.select_for_update().select_related('villa', 'villa__business').get(pk=partition.pk)
        tenant = locked.current_tenant
        if tenant is None:
            raise PartitionVacant(f'{locked} has no active tenant — nothing to collect.')
        invoice = Invoice.objects.filter(partition=locked, billing_period_start=start, billing_period_end=end, is_cancelled=False).first()
        if invoice is None:
            if not Charge.objects.filter(partition=locked, is_active=True).exists():
                ensure_rent_charge(partition=locked, tenant=tenant, created_by=collected_by)
            invoice = generate_monthly_invoice(partition=locked, billing_period_start=start, billing_period_end=end, issue_date=start, due_date=min(end, start + timedelta(days=9)), generated_by=collected_by)
        outstanding = selectors.invoice_outstanding(invoice)
        if outstanding <= 0:
            raise NothingToCollect(f'{locked.name}: rent for {start:%B %Y} is already fully collected, or there is nothing to bill.')
        return record_payment(invoice=invoice, amount=outstanding, method=method, collected_by=collected_by, collected_at=date.today(), created_by=collected_by)
