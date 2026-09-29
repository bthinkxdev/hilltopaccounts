from django.contrib.contenttypes.models import ContentType
from . import context
from .models import AuditLog

def log(*, user, action, obj=None, business=None, villa=None, old_value=None, new_value=None, reason=''):
    ctx = context.get_context()
    content_type = ContentType.objects.get_for_model(obj) if obj is not None else None
    object_id = str(obj.pk) if obj is not None else ''
    return AuditLog.objects.create(user=user, action=action, content_type=content_type, object_id=object_id, business=business, villa=villa, old_value=old_value, new_value=new_value, reason=reason, ip_address=ctx.ip_address, user_agent=ctx.user_agent, request_id=ctx.request_id)
