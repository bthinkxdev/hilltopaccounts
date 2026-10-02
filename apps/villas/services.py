from django.db import transaction
from apps.audit import services as audit_services
from apps.audit.models import Action
from django.core.exceptions import PermissionDenied
from apps.accounts import selectors as access_selectors
from .models import Partition, Photo, Villa
from .photos import validate_photo
TRACKED_FIELDS = ['name', 'address', 'landlord_name', 'landlord_contact', 'contract_start', 'contract_end']
PARTITION_TRACKED_FIELDS = ['name', 'description', 'rent', 'status']

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
    """Only the Owner or the villa's Business Manager may remove a photo; the audit trail keeps what was removed."""
    villa = photo.villa
    if not (deleted_by.is_owner or access_selectors.is_business_manager_of(deleted_by, villa.business)):
        raise PermissionDenied('You cannot delete this photo.')
    with transaction.atomic():
        audit_services.log(user=deleted_by, action=Action.DOCUMENT_DELETED, obj=photo, business=villa.business, villa=villa, old_value={'partition': photo.partition.name if photo.partition_id else None, 'caption': photo.caption, 'file': photo.image.name}, reason=reason or 'removed')
        photo.image.delete(save=False)
        photo.delete()
