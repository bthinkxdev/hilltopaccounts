import time
from . import services

SYNC_INTERVAL_SECONDS = 300

def notifications(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    now = time.time()
    last_sync = request.session.get('notifications_synced_at', 0)
    if now - last_sync > SYNC_INTERVAL_SECONDS:
        services.sync_for_user(user)
        request.session['notifications_synced_at'] = now
    return {'notification_unread_count': services.unread_count(user)}
