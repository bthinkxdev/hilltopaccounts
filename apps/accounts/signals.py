from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from apps.audit import context as audit_context
from apps.audit import services as audit_services
from apps.audit.models import Action
from .models import User
_TRACKED_FIELDS = ['email', 'first_name', 'last_name', 'is_active', 'is_owner']

@receiver(pre_save, sender=User)
def _stash_old_state(sender, instance, **kwargs):
    if not instance.pk:
        instance._audit_old_state = None
        return
    try:
        old = User.objects.get(pk=instance.pk)
    except User.DoesNotExist:
        instance._audit_old_state = None
    else:
        instance._audit_old_state = {field: getattr(old, field) for field in _TRACKED_FIELDS}

@receiver(post_save, sender=User)
def _log_change(sender, instance, created, **kwargs):
    from .services import SYSTEM_USERNAME, get_system_user
    if instance.username == SYSTEM_USERNAME:
        return
    if getattr(instance, '_skip_audit_signal', False):
        return
    actor = audit_context.get_context().actor or get_system_user()
    new_state = {field: getattr(instance, field) for field in _TRACKED_FIELDS}
    if created:
        audit_services.log(user=actor, action=Action.USER_CREATED, obj=instance, new_value=new_state)
        return
    old_state = getattr(instance, '_audit_old_state', None)
    if not old_state or old_state == new_state:
        return
    if old_state['is_active'] and (not new_state['is_active']):
        action = Action.USER_DISABLED
    elif old_state['is_owner'] != new_state['is_owner']:
        action = Action.ROLE_CHANGED
    else:
        action = Action.USER_UPDATED
    audit_services.log(user=actor, action=action, obj=instance, old_value=old_state, new_value=new_state)
