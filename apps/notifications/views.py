from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from apps.shared.pagination import paginate_queryset
from . import services
from .models import Notification

@login_required
def notification_list(request):
    services.sync_for_user(request.user)
    qs = Notification.objects.filter(recipient=request.user, resolved_at__isnull=True)
    show = request.GET.get('show', '')
    if show == 'unread':
        qs = qs.filter(read_at__isnull=True)
    page_obj = paginate_queryset(request, qs)
    return render(request, 'notifications/list.html', {'page_obj': page_obj, 'show': show, 'unread_total': services.unread_count(request.user), 'breadcrumbs': [('Notifications', None)]})

@login_required
@require_POST
def notification_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    services.mark_read(user=request.user, notification_id=notification.pk)
    return redirect('notifications:list')

@login_required
@require_POST
def notification_read_all(request):
    count = services.mark_read(user=request.user)
    messages.success(request, f'{count} notification{"s" if count != 1 else ""} marked as read.')
    return redirect('notifications:list')
