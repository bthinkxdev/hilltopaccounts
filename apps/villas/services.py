from django.db import transaction
from apps.audit import services as audit_services
from apps.audit.models import Action
from .models import Partition, Villa
TRACKED_FIELDS = ['name', 'address', 'landlord_name', 'landlord_contact', 'contract_start', 'contract_end']
PARTITION_TRACKED_FIELDS = ['name', 'description', 'status']

def create_villa(*, business, name, created_by, **fields) -> Villa:
    with transaction.atomic():
        villa = Villa(business=business, name=name, created_by=created_by, **fields)
        villa.full_clean()
        villa.save()
        audit_services.log(user=created_by, action=Action.VILLA_CREATED, obj=villa, business=business, villa=villa, new_value={f: str(getattr(villa, f)) for f in TRACKED_FIELDS})
    return villa

def update_villa(*, villa: Villa, updated_by, **fields) -> Villa:
    old_value = {f: str(getattr(villa, f)) for f in TRACKED_FIELDS}
    for field, value in fields.items():
        setattr(villa, field, value)
    with transaction.atomic():
        villa.full_clean()
        villa.save()
        new_value = {f: str(getattr(villa, f)) for f in TRACKED_FIELDS}
        if new_value != old_value:
            audit_services.log(user=updated_by, action=Action.VILLA_UPDATED, obj=villa, business=villa.business, villa=villa, old_value=old_value, new_value=new_value)
    return villa

def archive_villa(*, villa: Villa, archived_by, reason='') -> Villa:
    with transaction.atomic():
        villa.is_archived = True
        villa.save(update_fields=['is_archived'])
        audit_services.log(user=archived_by, action=Action.VILLA_ARCHIVED, obj=villa, business=villa.business, villa=villa, old_value={'is_archived': False}, new_value={'is_archived': True}, reason=reason)
    return villa

def create_partition(*, villa: Villa, name, created_by, **fields) -> Partition:
    with transaction.atomic():
        partition = Partition(villa=villa, name=name, created_by=created_by, **fields)
        partition.full_clean()
        partition.save()
        audit_services.log(user=created_by, action=Action.PARTITION_CREATED, obj=partition, business=villa.business, villa=villa, new_value={f: str(getattr(partition, f)) for f in PARTITION_TRACKED_FIELDS})
    return partition

def update_partition(*, partition: Partition, updated_by, **fields) -> Partition:
    old_value = {f: str(getattr(partition, f)) for f in PARTITION_TRACKED_FIELDS}
    for field, value in fields.items():
        setattr(partition, field, value)
    partition.updated_by = updated_by
    with transaction.atomic():
        partition.full_clean()
        partition.save()
        new_value = {f: str(getattr(partition, f)) for f in PARTITION_TRACKED_FIELDS}
        if new_value != old_value:
            audit_services.log(user=updated_by, action=Action.PARTITION_UPDATED, obj=partition, business=partition.villa.business, villa=partition.villa, old_value=old_value, new_value=new_value)
    return partition

def archive_partition(*, partition: Partition, archived_by, reason='') -> Partition:
    with transaction.atomic():
        partition.status = Partition.Status.ARCHIVED
        partition.updated_by = archived_by
        partition.save(update_fields=['status', 'updated_by', 'updated_at'])
        audit_services.log(user=archived_by, action=Action.PARTITION_ARCHIVED, obj=partition, business=partition.villa.business, villa=partition.villa, old_value={'status': Partition.Status.ACTIVE}, new_value={'status': Partition.Status.ARCHIVED}, reason=reason)
    return partition
