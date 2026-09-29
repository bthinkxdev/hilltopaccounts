from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from apps.shared.pagination import paginate_queryset
from .models import Action, AuditLog

@login_required
def audit_list(request):
    if not request.user.is_owner:
        raise PermissionDenied('Only the Owner can view the audit trail.')
    qs = AuditLog.objects.select_related('user', 'content_type', 'business', 'villa')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(user__username__icontains=q)
    action = request.GET.get('action', '')
    if action:
        qs = qs.filter(action=action)
    page_obj = paginate_queryset(request, qs.order_by('-timestamp'), per_page=50)
    return render(request, 'audit/list.html', {'page_obj': page_obj, 'q': q, 'action': action, 'action_choices': Action.choices, 'breadcrumbs': [('Audit Log', None)]})
