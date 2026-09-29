from django.db.models import Q, QuerySet
from apps.billing.models import Charge, Invoice, Payment
from apps.businesses.models import Business
from apps.cash.models import CashHandover
from apps.expenses.models import Expense
from apps.tenancy.models import Tenant
from apps.villas.models import Partition, Villa
from .models import Assignment

def businesses_visible_to(user) -> QuerySet[Business]:
    if user.is_owner:
        return Business.objects.all()
    return Business.objects.filter(Q(manager_assignments__user=user) | Q(villas__staff_assignments__user=user)).distinct()

def villas_visible_to(user) -> QuerySet[Villa]:
    if user.is_owner:
        return Villa.objects.all()
    return Villa.objects.filter(Q(business__manager_assignments__user=user) | Q(staff_assignments__user=user)).distinct()

def partitions_visible_to(user) -> QuerySet[Partition]:
    if user.is_owner:
        return Partition.objects.all()
    return Partition.objects.filter(villa__in=villas_visible_to(user))

def tenants_visible_to(user) -> QuerySet[Tenant]:
    if user.is_owner:
        return Tenant.objects.all()
    return Tenant.objects.filter(partition__in=partitions_visible_to(user))

def charges_visible_to(user) -> QuerySet[Charge]:
    if user.is_owner:
        return Charge.objects.all()
    return Charge.objects.filter(partition__in=partitions_visible_to(user))

def invoices_visible_to(user) -> QuerySet[Invoice]:
    if user.is_owner:
        return Invoice.objects.all()
    return Invoice.objects.filter(partition__in=partitions_visible_to(user))

def payments_visible_to(user) -> QuerySet[Payment]:
    if user.is_owner:
        return Payment.objects.all()
    return Payment.objects.filter(invoice__partition__in=partitions_visible_to(user))

def businesses_managed_by(user) -> QuerySet[Business]:
    if user.is_owner:
        return Business.objects.all()
    return Business.objects.filter(manager_assignments__user=user).distinct()

def expenses_visible_to(user) -> QuerySet[Expense]:
    if user.is_owner:
        return Expense.objects.all()
    return Expense.objects.filter(Q(villa__in=villas_visible_to(user)) | Q(villa__isnull=True, business__in=businesses_managed_by(user))).distinct()

def cash_handovers_visible_to(user) -> QuerySet[CashHandover]:
    if user.is_owner:
        return CashHandover.objects.all()
    managed_businesses = businesses_managed_by(user)
    if managed_businesses.exists():
        staff_ids = Assignment.objects.filter(villa__business__in=managed_businesses).values_list('user_id', flat=True)
        return CashHandover.objects.filter(Q(staff=user) | Q(staff_id__in=staff_ids)).distinct()
    return CashHandover.objects.filter(staff=user)

def is_business_manager_of(user, business) -> bool:
    if user.is_owner:
        return True
    return Assignment.objects.filter(user=user, role=Assignment.Role.BUSINESS_MANAGER, business=business).exists()

def is_villa_staff_of(user, villa) -> bool:
    if user.is_owner:
        return True
    return Assignment.objects.filter(user=user, role=Assignment.Role.VILLA_STAFF, villa=villa).exists()

def is_accountant_for(user, *, business=None, villa=None) -> bool:
    if user.is_owner:
        return True
    qs = Assignment.objects.filter(user=user, role=Assignment.Role.ACCOUNTANT)
    if business is not None:
        qs = qs.filter(Q(business=business) | Q(villa__business=business))
    if villa is not None:
        qs = qs.filter(Q(villa=villa) | Q(business=villa.business))
    return qs.exists()

def can_view_financial_kpis(user) -> bool:
    if user.is_owner:
        return True
    return Assignment.objects.filter(user=user, role__in=[Assignment.Role.BUSINESS_MANAGER, Assignment.Role.ACCOUNTANT]).exists()

def can_manage_villas_and_tenants(user) -> bool:
    if user.is_owner:
        return True
    return Assignment.objects.filter(user=user, role__in=[Assignment.Role.BUSINESS_MANAGER, Assignment.Role.VILLA_STAFF]).exists()

def role_labels_for(user) -> list[str]:
    if user.is_owner:
        return ['Owner']
    roles = Assignment.objects.filter(user=user).values_list('role', flat=True).distinct()
    return [Assignment.Role(role).label for role in roles]
