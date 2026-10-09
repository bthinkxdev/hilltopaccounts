from decimal import Decimal
from django.db import transaction
from apps.audit import services as audit_services
from apps.audit.models import Action
from apps.shared.exceptions import DomainError
from .exceptions import PartitionAlreadyOccupied
from .models import Tenant
TRACKED_FIELDS = ['name', 'mobile', 'id_document_number', 'nationality', 'move_in_date', 'move_out_date', 'monthly_rent', 'deposit', 'status', 'notes']

def _snapshot(tenant: Tenant) -> dict:
    return {f: str(getattr(tenant, f)) for f in TRACKED_FIELDS}

def create_tenant(*, partition, name, move_in_date, monthly_rent, created_by, mobile='', id_document_number='', nationality='', deposit=Decimal('0.00'), notes='') -> Tenant:
    if partition.is_occupied:
        raise PartitionAlreadyOccupied(f'{partition} already has an active tenant ({partition.current_tenant}).')
    with transaction.atomic():
        tenant = Tenant(partition=partition, name=name, move_in_date=move_in_date, monthly_rent=monthly_rent, mobile=mobile, id_document_number=id_document_number, nationality=nationality, deposit=deposit, notes=notes, created_by=created_by)
        tenant.full_clean()
        tenant.save()
        if partition.rent is None:
            partition.rent = monthly_rent
            partition.save(update_fields=['rent'])
        from apps.billing.services import ensure_rent_charge
        ensure_rent_charge(partition=partition, tenant=tenant, created_by=created_by)
        audit_services.log(user=created_by, action=Action.TENANT_CREATED, obj=tenant, business=partition.villa.business, villa=partition.villa, new_value=_snapshot(tenant))
    return tenant

def update_tenant(*, tenant: Tenant, updated_by, **fields) -> Tenant:
    old_value = _snapshot(tenant)
    for field, value in fields.items():
        setattr(tenant, field, value)
    tenant.updated_by = updated_by
    with transaction.atomic():
        tenant.full_clean()
        tenant.save()
        new_value = _snapshot(tenant)
        if new_value != old_value:
            audit_services.log(user=updated_by, action=Action.TENANT_UPDATED, obj=tenant, business=tenant.partition.villa.business, villa=tenant.partition.villa, old_value=old_value, new_value=new_value)
    return tenant

def move_out_tenant(*, tenant: Tenant, move_out_date, moved_out_by, reason='') -> Tenant:
    if tenant.status != Tenant.Status.ACTIVE:
        raise DomainError(f'{tenant} is not an active tenancy — cannot move out.')
    if move_out_date < tenant.move_in_date:
        raise DomainError('Move-out date cannot be before the move-in date.')
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
