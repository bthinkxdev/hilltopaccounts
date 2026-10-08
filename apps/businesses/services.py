from django.db import transaction
from apps.audit import services as audit_services
from apps.audit.models import Action
from apps.shared.exceptions import DomainError
from .models import Business

def create_business(*, name, description='', created_by) -> Business:
    with transaction.atomic():
        business = Business(name=name, description=description, created_by=created_by)
        business.full_clean()
        business.save()
        audit_services.log(user=created_by, action=Action.BUSINESS_CREATED, obj=business, business=business, new_value={'name': business.name, 'description': business.description})
    return business

def update_business(*, business: Business, name=None, description=None, updated_by) -> Business:
    old_value = {'name': business.name, 'description': business.description}
    if name is not None:
        business.name = name
    if description is not None:
        business.description = description
    with transaction.atomic():
        business.full_clean()
        business.save()
        new_value = {'name': business.name, 'description': business.description}
        if new_value != old_value:
            audit_services.log(user=updated_by, action=Action.BUSINESS_UPDATED, obj=business, business=business, old_value=old_value, new_value=new_value)
    return business

def archive_business(*, business: Business, archived_by, reason='') -> Business:
    from apps.tenancy.models import Tenant
    active_tenants = list(Tenant.objects.filter(partition__villa__business=business, status=Tenant.Status.ACTIVE).select_related('partition__villa'))
    if active_tenants:
        if len(active_tenants) == 1:
            t = active_tenants[0]
            raise DomainError(f'{business.name} has an active tenant ({t.name} in {t.partition.name}, {t.partition.villa.name}). Move the tenant out before archiving.')
        raise DomainError(f'{business.name} has {len(active_tenants)} active tenants across its villas. Move all tenants out before archiving.')
    with transaction.atomic():
        business.is_archived = True
        business.save(update_fields=['is_archived'])
        audit_services.log(user=archived_by, action=Action.BUSINESS_ARCHIVED, obj=business, business=business, old_value={'is_archived': False}, new_value={'is_archived': True}, reason=reason)
    return business

def unarchive_business(*, business: Business, unarchived_by) -> Business:
    with transaction.atomic():
        business.is_archived = False
        business.save(update_fields=['is_archived'])
        audit_services.log(user=unarchived_by, action=Action.BUSINESS_UPDATED, obj=business, business=business, old_value={'is_archived': True}, new_value={'is_archived': False}, reason='Unarchived')
    return business
