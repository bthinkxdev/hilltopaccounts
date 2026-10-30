from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from apps.shared.pagination import paginate_queryset
from apps.shared.periods import period_context
from .models import Action, AuditLog

@login_required
def audit_list(request):
    if not request.user.is_owner:
        raise PermissionDenied('Only the Admin can view the audit trail.')
    qs = AuditLog.objects.select_related('user', 'content_type', 'business', 'villa').prefetch_related('content_object')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(user__username__icontains=q)
    action = request.GET.get('action', '')
    if action:
        qs = qs.filter(action=action)
    period = period_context(request)
    qs = qs.filter(timestamp__date__range=(period['period_start'], period['period_end']))
    page_obj = paginate_queryset(request, qs.order_by('-timestamp'), per_page=50)
    return render(request, 'audit/list.html', {**period, 'page_obj': page_obj, 'q': q, 'action': action, 'action_choices': Action.choices, 'breadcrumbs': [('Audit Log', None)]})
