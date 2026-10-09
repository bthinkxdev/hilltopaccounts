from django.db import transaction
from apps.audit import services as audit_services
from apps.audit.models import Action
from django.core.exceptions import PermissionDenied
from apps.accounts import selectors as access_selectors
from apps.shared.exceptions import DomainError
from .models import Partition, Photo, Villa
from .photos import validate_photo
TRACKED_FIELDS = ['name', 'address', 'landlord_name', 'landlord_contact', 'contract_start', 'contract_end']
PARTITION_TRACKED_FIELDS = ['name', 'description', 'rent', 'status']

def create_villa(*, business, name, created_by, **fields) -> Villa:
    if business.is_archived:
        raise DomainError(f'Cannot create a villa under archived business “{business.name}”.')
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
    from apps.tenancy.models import Tenant
    active_tenants = list(Tenant.objects.filter(partition__villa=villa, status=Tenant.Status.ACTIVE).select_related('partition'))
    if active_tenants:
        if len(active_tenants) == 1:
            t = active_tenants[0]
            raise DomainError(f'{villa.name} is occupied by {t.name} in {t.partition.name}. Move the tenant out before archiving.')
        raise DomainError(f'{villa.name} has {len(active_tenants)} active tenants across its partitions. Move all tenants out before archiving.')
    with transaction.atomic():
        villa.is_archived = True
        villa.save(update_fields=['is_archived'])
        audit_services.log(user=archived_by, action=Action.VILLA_ARCHIVED, obj=villa, business=villa.business, villa=villa, old_value={'is_archived': False}, new_value={'is_archived': True}, reason=reason)
    return villa

def unarchive_villa(*, villa: Villa, unarchived_by) -> Villa:
    if villa.business.is_archived:
        raise DomainError(f'Cannot unarchive villa while its parent business “{villa.business.name}” is archived.')
    with transaction.atomic():
        villa.is_archived = False
        villa.save(update_fields=['is_archived'])
        audit_services.log(user=unarchived_by, action=Action.VILLA_UPDATED, obj=villa, business=villa.business, villa=villa, old_value={'is_archived': True}, new_value={'is_archived': False}, reason='Unarchived')
    return villa

def create_partition(*, villa: Villa, name, created_by, **fields) -> Partition:
    if villa.is_archived or villa.business.is_archived:
        raise DomainError(f'Cannot create a partition under archived villa “{villa.name}”.')
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
    if partition.is_occupied:
        raise DomainError(f'{partition} is occupied by {partition.current_tenant}. Move the tenant out before archiving.')
    with transaction.atomic():
        partition.status = Partition.Status.ARCHIVED
        partition.updated_by = archived_by
        partition.save(update_fields=['status', 'updated_by', 'updated_at'])
        audit_services.log(user=archived_by, action=Action.PARTITION_ARCHIVED, obj=partition, business=partition.villa.business, villa=partition.villa, old_value={'status': Partition.Status.ACTIVE}, new_value={'status': Partition.Status.ARCHIVED}, reason=reason)
    return partition

def unarchive_partition(*, partition: Partition, unarchived_by) -> Partition:
    if partition.villa.is_archived:
        raise DomainError(f'Cannot unarchive partition while its parent villa “{partition.villa.name}” is archived.')
    with transaction.atomic():
        partition.status = Partition.Status.ACTIVE
        partition.updated_by = unarchived_by
        partition.save(update_fields=['status', 'updated_by', 'updated_at'])
        audit_services.log(user=unarchived_by, action=Action.PARTITION_UPDATED, obj=partition, business=partition.villa.business, villa=partition.villa, old_value={'status': Partition.Status.ARCHIVED}, new_value={'status': Partition.Status.ACTIVE}, reason='Unarchived')
    return partition


def add_photo(*, villa, image, uploaded_by, partition=None, caption='') -> Photo:
    """Owner, the villa's Business Manager and its assigned Villa Staff may add photos."""
    if not access_selectors.can_manage_villa(uploaded_by, villa):
        raise PermissionDenied('You cannot add photos to this villa.')
    validate_photo(image)
    with transaction.atomic():
        photo = Photo(villa=villa, partition=partition, image=image, caption=caption, uploaded_by=uploaded_by)
        photo.full_clean()
        photo.save()
        audit_services.log(user=uploaded_by, action=Action.DOCUMENT_UPLOADED, obj=photo, business=villa.business, villa=villa, new_value={'partition': partition.name if partition else None, 'caption': caption, 'file': photo.image.name})
    return photo

def delete_photo(*, photo: Photo, deleted_by, reason='') -> None:
    villa = photo.villa
    if not access_selectors.can_delete_photo(deleted_by, photo):
        raise PermissionDenied('You cannot delete this photo.')
    with transaction.atomic():
        audit_services.log(user=deleted_by, action=Action.DOCUMENT_DELETED, obj=photo, business=villa.business, villa=villa, old_value={'partition': photo.partition.name if photo.partition_id else None, 'caption': photo.caption, 'file': photo.image.name}, reason=reason or 'removed')
        try:
            photo.image.delete(save=False)
        except OSError:
            pass
        photo.delete()
