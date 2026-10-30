from datetime import date
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from apps.audit import services as audit_services
from apps.audit.models import Action
from apps.shared.exceptions import DomainError
from .exceptions import PartitionAlreadyOccupied
from .models import DepositCollection, Tenant
TRACKED_FIELDS = ['name', 'mobile', 'id_document_number', 'nationality', 'move_in_date', 'move_out_date', 'monthly_rent', 'deposit', 'deposit_paid', 'deposit_status', 'status', 'notes']

def _snapshot(tenant: Tenant) -> dict:
    return {f: str(getattr(tenant, f)) for f in TRACKED_FIELDS}

def create_tenant(*, partition, name, move_in_date, monthly_rent, created_by, mobile='', id_document_number='', nationality='', deposit=None, deposit_paid=None, deposit_method=DepositCollection.Method.CASH, notes='') -> Tenant:
    """Onboarding: fix the monthly rent and the advance (0 = no advance). Any advance received now is recorded
    as a dated collection; the rest stays pending and is collected later, in parts if needed."""
    if partition.villa.is_sold:
        raise DomainError(f'Cannot add tenants to sold villa “{partition.villa.name}”.')
    deposit = deposit or Decimal('0.00')
    deposit_paid = deposit_paid or Decimal('0.00')
    if deposit_paid > deposit:
        raise DomainError(f'Advance received (QAR {deposit_paid}) cannot be more than the advance (QAR {deposit}).')
    if partition.is_occupied:
        raise PartitionAlreadyOccupied(f'{partition} already has an active tenant ({partition.current_tenant}).')
    with transaction.atomic():
        tenant = Tenant(partition=partition, name=name, move_in_date=move_in_date, monthly_rent=monthly_rent, mobile=mobile, id_document_number=id_document_number, nationality=nationality, deposit=deposit, notes=notes, created_by=created_by)
        tenant.full_clean()
        tenant.save()
        if deposit_paid > 0:
            _record_deposit(tenant, amount=deposit_paid, method=deposit_method, collected_at=min(move_in_date, date.today()), collected_by=created_by)
        if partition.rent is None:
            partition.rent = monthly_rent
            partition.save(update_fields=['rent'])
        from apps.billing.services import ensure_rent_charge
        ensure_rent_charge(partition=partition, tenant=tenant, created_by=created_by)
        audit_services.log(user=created_by, action=Action.TENANT_CREATED, obj=tenant, business=partition.villa.business, villa=partition.villa, new_value=_snapshot(tenant))
    return tenant

def _record_deposit(tenant: Tenant, *, amount, method, collected_at, collected_by) -> DepositCollection:
    collection = DepositCollection(tenant=tenant, amount=amount, method=method, collected_at=collected_at, collected_by=collected_by)
    collection.full_clean()
    collection.save()
    old_paid = tenant.deposit_paid
    tenant.deposit_paid = old_paid + amount
    tenant.save(update_fields=['deposit_paid', 'deposit_status', 'updated_at'])
    audit_services.log(user=collected_by, action=Action.TENANT_UPDATED, obj=tenant, business=tenant.partition.villa.business, villa=tenant.partition.villa, old_value={'deposit_paid': str(old_paid)}, new_value={'deposit_paid': str(tenant.deposit_paid), 'collected': str(amount), 'method': method, 'collected_at': str(collected_at)}, reason=f'Advance collected: QAR {amount}')
    return collection

def collect_deposit(*, tenant: Tenant, amount, method, collected_by, collected_at=None) -> DepositCollection:
    """Collect part or all of the pending advance. The amount is added to what was already collected."""
    collected_at = collected_at or date.today()
    if tenant.partition.villa.is_sold:
        raise DomainError(f'Cannot collect an advance in sold villa “{tenant.partition.villa.name}”.')
    if amount is None or amount <= 0:
        raise DomainError('Enter the advance amount received.')
    if collected_at > date.today():
        raise DomainError('Collection date cannot be in the future.')
    with transaction.atomic():
        locked = Tenant.objects.select_for_update().select_related('partition__villa__business').get(pk=tenant.pk)
        if amount > locked.deposit_pending:
            raise DomainError(f'Only QAR {locked.deposit_pending} of the advance is pending.')
        collection = _record_deposit(locked, amount=amount, method=method, collected_at=collected_at, collected_by=collected_by)
    tenant.refresh_from_db()
    return collection

def void_deposit(*, collection: DepositCollection, voided_by, reason, _action=Action.DEPOSIT_COLLECTION_VOIDED) -> DepositCollection:
    """Cancel a wrongly recorded advance collection. The row is kept (marked voided, with who/when/why) and drops out of
    the tenant's received amount, income, profit and cash totals. Refused once the cash is part of a live handover."""
    from apps.cash.models import CashHandover
    reason = (reason or '').strip()
    if not reason:
        raise DomainError('A reason is required to cancel an advance collection.')
    with transaction.atomic():
        locked = DepositCollection.all_objects.select_for_update().select_related('tenant__partition__villa__business').get(pk=collection.pk)
        if locked.is_voided:
            raise DomainError('This advance collection is already cancelled.')
        villa = locked.tenant.partition.villa
        if villa.is_sold:
            raise DomainError(f'Cannot change advance collections of sold villa “{villa.name}”.')
        if locked.handovers.filter(status=CashHandover.Status.CONFIRMED).exists():
            raise DomainError('This advance is part of a confirmed cash handover and cannot be cancelled directly.')
        if locked.handovers.filter(status=CashHandover.Status.SUBMITTED).exists():
            raise DomainError('This advance is part of a pending cash handover. Reject the handover first.')
        tenant = Tenant.objects.select_for_update().get(pk=locked.tenant_id)
        old_paid = tenant.deposit_paid
        locked.voided_at = timezone.now()
        locked.voided_by = voided_by
        locked.void_reason = reason
        locked.save(update_fields=['voided_at', 'voided_by', 'void_reason'])
        tenant.deposit_paid = max(Decimal('0.00'), old_paid - locked.amount)
        tenant.save(update_fields=['deposit_paid', 'deposit_status', 'updated_at'])
        audit_services.log(user=voided_by, action=_action, obj=locked, business=villa.business, villa=villa, old_value={'voided': False, 'amount': str(locked.amount), 'collected_at': str(locked.collected_at), 'method': locked.method, 'tenant_deposit_paid': str(old_paid)}, new_value={'voided': True, 'tenant_deposit_paid': str(tenant.deposit_paid)}, reason=reason)
    collection.voided_at, collection.voided_by, collection.void_reason = locked.voided_at, voided_by, reason
    return collection

def correct_deposit(*, collection: DepositCollection, new_amount, voided_by, reason, method=None, collected_at=None) -> DepositCollection:
    """Fix a wrong amount/method/date: void the original and record the replacement in one transaction."""
    if new_amount is None or new_amount <= 0:
        raise DomainError('Enter the corrected advance amount.')
    collected_at = collected_at or collection.collected_at
    if collected_at > date.today():
        raise DomainError('Collection date cannot be in the future.')
    with transaction.atomic():
        void_deposit(collection=collection, voided_by=voided_by, reason=f'corrected: {reason}', _action=Action.DEPOSIT_COLLECTION_CORRECTED)
        locked = Tenant.objects.select_for_update().select_related('partition__villa__business').get(pk=collection.tenant_id)
        if new_amount > locked.deposit_pending:
            raise DomainError(f'Only QAR {locked.deposit_pending} of the advance can be collected after cancelling this entry.')
        return _record_deposit(locked, amount=new_amount, method=method or collection.method, collected_at=collected_at, collected_by=collection.collected_by)

def update_tenant(*, tenant: Tenant, updated_by, **fields) -> Tenant:
    if tenant.partition.villa.is_sold:
        raise DomainError(f'Cannot edit tenants of sold villa “{tenant.partition.villa.name}”.')
    old_value = _snapshot(tenant)
    rent_changed = 'monthly_rent' in fields and fields['monthly_rent'] != tenant.monthly_rent
    if 'deposit' in fields and fields['deposit'] < tenant.deposit_paid:
        raise DomainError(f'The advance cannot be less than what was already collected (QAR {tenant.deposit_paid}).')
    for field, value in fields.items():
        setattr(tenant, field, value)
    tenant.updated_by = updated_by
    tenant.rent_effective_from = None
    with transaction.atomic():
        tenant.full_clean()
        tenant.save()
        if rent_changed and tenant.status == Tenant.Status.ACTIVE:
            from apps.billing.services import change_rent
            tenant.rent_effective_from = change_rent(partition=tenant.partition, tenant=tenant, new_amount=tenant.monthly_rent, changed_by=updated_by)
        new_value = _snapshot(tenant)
        if new_value != old_value:
            audit_services.log(user=updated_by, action=Action.TENANT_UPDATED, obj=tenant, business=tenant.partition.villa.business, villa=tenant.partition.villa, old_value=old_value, new_value=new_value)
    return tenant

def move_out_tenant(*, tenant: Tenant, move_out_date, moved_out_by, reason='') -> Tenant:
    if tenant.partition.villa.is_sold:
        raise DomainError(f'Cannot move out tenants of sold villa “{tenant.partition.villa.name}”.')
    if tenant.status != Tenant.Status.ACTIVE:
        raise DomainError(f'{tenant} is not an active tenancy — cannot move out.')
    if move_out_date < tenant.move_in_date:
        raise DomainError('Move-out date cannot be before the move-in date.')
    from apps.billing.services import ensure_tenant_invoices
    ensure_tenant_invoices(tenant, up_to=min(move_out_date, date.today()), generated_by=moved_out_by)
    old_value = _snapshot(tenant)
    tenant.status = Tenant.Status.MOVED_OUT
    tenant.move_out_date = move_out_date
    tenant.updated_by = moved_out_by
    with transaction.atomic():
        tenant.full_clean()
        tenant.save()
        from apps.billing.services import close_future_charges
        close_future_charges(partition=tenant.partition, move_out_date=move_out_date, closed_by=moved_out_by)
        audit_services.log(user=moved_out_by, action=Action.TENANT_MOVED_OUT, obj=tenant, business=tenant.partition.villa.business, villa=tenant.partition.villa, old_value=old_value, new_value=_snapshot(tenant), reason=reason)
    return tenant
