from . import selectors
from .models import Assignment

def nav(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    has_any_scope = user.is_owner or selectors.villas_visible_to(user).exists()
    return {'nav_has_scope': has_any_scope, 'nav_can_view_financials': selectors.can_view_financial_kpis(user), 'nav_is_field_staff': selectors.is_field_staff(user), 'nav_can_manage_users': user.is_owner, 'nav_can_manage_operations': selectors.can_manage_villas_and_tenants(user), 'nav_pending_handover_count': _pending_handover_count(user) if has_any_scope else 0}

def _pending_handover_count(user):
    from apps.cash.models import CashHandover
    qs = selectors.cash_handovers_visible_to(user).filter(status=CashHandover.Status.SUBMITTED)
    if not user.is_owner and (not Assignment.objects.filter(user=user, role__in=[Assignment.Role.BUSINESS_MANAGER, Assignment.Role.ACCOUNTANT]).exists()):
        return 0
    return qs.count()
