from django.db import transaction
from apps.audit import services as audit_services
from apps.audit.models import Action
from .models import Assignment, User
SYSTEM_USERNAME = 'system'

def get_system_user() -> User:
    user, created = User.objects.get_or_create(username=SYSTEM_USERNAME, defaults={'first_name': 'System', 'last_name': 'Process', 'is_active': False})
    if created:
        user.set_unusable_password()
        user.save(update_fields=['password'])
    return user

def create_user(*, username, email='', first_name='', last_name='', is_owner=False, created_by, password=None) -> User:
    with transaction.atomic():
        user = User(username=username, email=email, first_name=first_name, last_name=last_name, is_owner=is_owner)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.full_clean()
        user._skip_audit_signal = True
        user.save()
        audit_services.log(user=created_by, action=Action.USER_CREATED, obj=user, new_value={'username': user.username, 'is_owner': user.is_owner})
    return user

def disable_user(*, user: User, disabled_by, reason='') -> User:
    with transaction.atomic():
        user.is_active = False
        user.save(update_fields=['is_active'])
        audit_services.log(user=disabled_by, action=Action.USER_DISABLED, obj=user, old_value={'is_active': True}, new_value={'is_active': False}, reason=reason)
    return user

def enable_user(*, user: User, enabled_by, reason='') -> User:
    with transaction.atomic():
        user.is_active = True
        user.save(update_fields=['is_active'])
        audit_services.log(user=enabled_by, action=Action.USER_UPDATED, obj=user, old_value={'is_active': False}, new_value={'is_active': True}, reason=reason or 'reactivated')
    return user

def _create_assignment(*, user, role, business, villa, assigned_by) -> Assignment:
    with transaction.atomic():
        assignment = Assignment(user=user, role=role, business=business, villa=villa, created_by=assigned_by)
        assignment.full_clean()
        assignment.save()
        action = Action.VILLA_ASSIGNMENT_CHANGED if villa is not None else Action.BUSINESS_ASSIGNMENT_CHANGED
        audit_services.log(user=assigned_by, action=action, obj=assignment, business=business or (villa.business if villa else None), villa=villa, new_value={'assigned_user': user.username, 'role': role})
    return assignment

def assign_business_manager(*, user, business, assigned_by) -> Assignment:
    return _create_assignment(user=user, role=Assignment.Role.BUSINESS_MANAGER, business=business, villa=None, assigned_by=assigned_by)

def assign_villa_staff(*, user, villa, assigned_by) -> Assignment:
    return _create_assignment(user=user, role=Assignment.Role.VILLA_STAFF, business=None, villa=villa, assigned_by=assigned_by)

def assign_accountant(*, user, assigned_by, business=None, villa=None) -> Assignment:
    return _create_assignment(user=user, role=Assignment.Role.ACCOUNTANT, business=business, villa=villa, assigned_by=assigned_by)

def remove_assignment(*, assignment: Assignment, removed_by, reason='') -> None:
    with transaction.atomic():
        old_value = {'assigned_user': assignment.user.username, 'role': assignment.role}
        action = Action.VILLA_ASSIGNMENT_CHANGED if assignment.villa_id else Action.BUSINESS_ASSIGNMENT_CHANGED
        business = assignment.business or (assignment.villa.business if assignment.villa_id else None)
        villa = assignment.villa
        audit_services.log(user=removed_by, action=action, obj=assignment, business=business, villa=villa, old_value=old_value, new_value=None, reason=reason or 'assignment removed')
        assignment.delete()
