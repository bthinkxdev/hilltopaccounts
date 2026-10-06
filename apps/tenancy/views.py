from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from apps.accounts import selectors
from apps.billing import selectors as billing_selectors
from apps.shared.exceptions import DomainError
from apps.shared.pagination import paginate_queryset
from .forms import MoveOutForm, TenantForm
from .models import Tenant
from .services import create_tenant, move_out_tenant

@login_required
def tenant_list(request):
    qs = selectors.tenants_visible_to(request.user).select_related('partition', 'partition__villa')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(mobile__icontains=q))
    status = request.GET.get('status', Tenant.Status.ACTIVE)
    if status:
        qs = qs.filter(status=status)
    page_obj = paginate_queryset(request, qs.order_by('-move_in_date'))
    return render(request, 'tenancy/list.html', {'page_obj': page_obj, 'q': q, 'status': status, 'breadcrumbs': [('Tenants', None)]})

@login_required
def tenant_detail(request, pk):
    tenant = get_object_or_404(selectors.tenants_visible_to(request.user), pk=pk)
    invoices = selectors.invoices_visible_to(request.user).filter(tenant=tenant).order_by('-issue_date')
    for invoice in invoices:
        invoice.computed_status = billing_selectors.invoice_status(invoice)
        invoice.computed_outstanding = billing_selectors.invoice_outstanding(invoice)
    balance = sum((inv.computed_outstanding for inv in invoices), start=0)
    partition = tenant.partition
    return render(request, 'tenancy/detail.html', {'tenant': tenant, 'invoices': invoices, 'outstanding_balance': balance, 'can_manage': request.user.is_owner or selectors.is_business_manager_of(request.user, partition.villa.business) or selectors.is_villa_staff_of(request.user, partition.villa), 'breadcrumbs': [('Businesses', reverse('businesses:list')), (partition.villa.business.name, reverse('businesses:detail', args=[partition.villa.business.pk])), (partition.villa.name, reverse('villas:villa_detail', args=[partition.villa.pk])), (partition.name, reverse('villas:partition_detail', args=[partition.pk])), (tenant.name, None)]})

@login_required
def tenant_create(request, partition_pk):
    partition = get_object_or_404(selectors.partitions_visible_to(request.user), pk=partition_pk)
    if not (request.user.is_owner or selectors.can_manage_villas_and_tenants(request.user)):
        raise PermissionDenied('You cannot move in a tenant here.')
    if request.method == 'POST':
        lock_rent = partition.rent is not None
        data = request.POST.copy()
        if lock_rent:
            data['monthly_rent'] = str(partition.rent)
        form = TenantForm(data, restrict_past_move_in=not request.user.is_owner, lock_rent=lock_rent)
        if form.is_valid():
            try:
                tenant = create_tenant(partition=partition, created_by=request.user, **form.cleaned_data)
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f'{tenant.name} moved in to {partition.name}.')
                return redirect('tenancy:detail', pk=tenant.pk)
    else:
        initial = {}
        if partition.rent is not None:
            initial['monthly_rent'] = partition.rent
        form = TenantForm(initial=initial, restrict_past_move_in=not request.user.is_owner, lock_rent=partition.rent is not None)
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Move In Tenant — {partition.name}', 'submit_label': 'Move In', 'cancel_url': reverse('villas:partition_detail', args=[partition.pk]), 'breadcrumbs': [('Businesses', reverse('businesses:list')), (partition.villa.business.name, reverse('businesses:detail', args=[partition.villa.business.pk])), (partition.villa.name, reverse('villas:villa_detail', args=[partition.villa.pk])), (partition.name, reverse('villas:partition_detail', args=[partition.pk])), ('Move In Tenant', None)]})

@login_required
def tenant_move_out(request, pk):
    tenant = get_object_or_404(selectors.tenants_visible_to(request.user), pk=pk)
    partition = tenant.partition
    if not (request.user.is_owner or selectors.can_manage_villas_and_tenants(request.user)):
        raise PermissionDenied('You cannot move out this tenant.')
    if request.method == 'POST':
        form = MoveOutForm(request.POST)
        if form.is_valid():
            try:
                move_out_tenant(tenant=tenant, move_out_date=form.cleaned_data['move_out_date'], moved_out_by=request.user, reason=form.cleaned_data['reason'])
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f'{tenant.name} moved out of {partition.name}.')
                return redirect('villas:partition_detail', pk=partition.pk)
    else:
        form = MoveOutForm()
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Move Out — {tenant.name}', 'submit_label': 'Move Out', 'cancel_url': reverse('tenancy:detail', args=[tenant.pk])})
