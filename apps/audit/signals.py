from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from . import services as audit_services
from .models import Action

@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    audit_services.log(user=user, action=Action.AUTH_LOGIN, obj=user)

@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is None:
        return
    audit_services.log(user=user, action=Action.AUTH_LOGOUT, obj=user)

@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    from apps.accounts.services import get_system_user
    username = credentials.get('username', '') or ''
    attempted_user = get_user_model().objects.filter(username=username).first()
    actor = attempted_user or get_system_user()
    audit_services.log(user=actor, action=Action.AUTH_FAILED_LOGIN, obj=attempted_user, reason=f'failed login attempt for username={username!r}')
