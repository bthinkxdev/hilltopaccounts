from django.db import transaction
from apps.audit import services as audit_services
from apps.audit.models import Action
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
    with transaction.atomic():
        business.is_archived = True
        business.save(update_fields=['is_archived'])
        audit_services.log(user=archived_by, action=Action.BUSINESS_ARCHIVED, obj=business, business=business, old_value={'is_archived': False}, new_value={'is_archived': True}, reason=reason)
    return business
