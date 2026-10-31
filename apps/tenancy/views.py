from datetime import date
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.forms.models import model_to_dict
from django.urls import reverse
from apps.accounts import selectors
from apps.billing import selectors as billing_selectors
from apps.shared.exceptions import DomainError
from apps.shared.pagination import paginate_queryset
from apps.villas.models import Partition
from apps.shared.forms import ReasonForm
from .forms import DepositCollectForm, DepositCorrectionForm, MoveOutForm, TenantForm
from .models import DepositCollection, Tenant
from .services import collect_deposit, correct_deposit, create_tenant, move_out_tenant, update_tenant, void_deposit

@login_required
def tenant_list(request):
    qs = selectors.tenants_visible_to(request.user).select_related('partition', 'partition__villa')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(mobile__icontains=q))
    status = request.GET.get('status', Tenant.Status.ACTIVE)
    if status == Tenant.Status.ACTIVE:
        qs = qs.filter(status=status, partition__villa__is_sold=False)
    elif status:
        qs = qs.filter(status=status)
    page_obj = paginate_queryset(request, qs.order_by('-move_in_date'))
    has_filters = bool(q or ('status' in request.GET and status != Tenant.Status.ACTIVE))
    return render(request, 'tenancy/list.html', {'page_obj': page_obj, 'q': q, 'status': status, 'has_filters': has_filters, 'breadcrumbs': [('Tenants', None)]})

@login_required
def tenant_detail(request, pk):
    today = date.today()
    tenant = get_object_or_404(selectors.tenants_visible_to(request.user), pk=pk)
    if tenant.status == Tenant.Status.ACTIVE and not tenant.partition.villa.is_sold:
        from apps.billing.services import ensure_rent_invoices
        ensure_rent_invoices(villa_ids=[tenant.partition.villa_id])
    invoices = selectors.invoices_visible_to(request.user).filter(tenant=tenant).order_by('-issue_date')
    for invoice in invoices:
        invoice.computed_status = billing_selectors.invoice_status(invoice)
        invoice.computed_outstanding = billing_selectors.invoice_outstanding(invoice)
    unbilled = Decimal('0.00')
    if tenant.status == Tenant.Status.ACTIVE:
        unbilled = billing_selectors.unbilled_charges_by_partition([tenant.partition_id], today.year, today.month)[tenant.partition_id] or (tenant.monthly_rent if not invoices.exists() else Decimal('0.00'))
    balance = sum((inv.computed_outstanding for inv in invoices), start=Decimal('0.00')) + unbilled
    partition = tenant.partition
    prefix = [('Villas', reverse('villas:villa_list'))] if not request.user.is_owner else [('Businesses', reverse('businesses:list')), (partition.villa.business.name, reverse('businesses:detail', args=[partition.villa.business.pk]))]
    return render(request, 'tenancy/detail.html', {
        'tenant': tenant,
        'invoices': invoices,
        'outstanding_balance': balance,
        'unbilled_balance': unbilled,
        'deposit_collections': list(DepositCollection.all_objects.filter(tenant=tenant).select_related('collected_by', 'voided_by')),
        'rent': billing_selectors.rent_schedule(partition, today) if tenant.status == Tenant.Status.ACTIVE else None,
        'can_manage': (request.user.is_owner or selectors.is_business_manager_of(request.user, partition.villa.business) or selectors.is_villa_staff_of(request.user, partition.villa)) and not partition.villa.is_sold,
        'breadcrumbs': prefix + [
            (partition.villa.name, reverse('villas:villa_detail', args=[partition.villa.pk])),
            (partition.name, reverse('villas:partition_detail', args=[partition.pk])),
            (tenant.name, None),
        ]
    })

@login_required
def tenant_edit(request, pk):
    tenant = get_object_or_404(selectors.tenants_visible_to(request.user), pk=pk, status=Tenant.Status.ACTIVE)
    partition = tenant.partition
    if partition.villa.is_sold:
        raise PermissionDenied('Cannot edit tenants of a sold villa.')
    if not (request.user.is_owner or selectors.can_manage_villa(request.user, partition.villa)):
        raise PermissionDenied('You cannot edit this tenant.')
    if request.method == 'POST':
        form = TenantForm(request.POST, editing=True)
        if form.is_valid():
            try:
                update_tenant(tenant=tenant, updated_by=request.user, **form.cleaned_data)
                if partition.rent is None:
                    partition.rent = form.cleaned_data['monthly_rent']
                    partition.save(update_fields=['rent'])
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                if tenant.rent_effective_from:
                    messages.success(request, f'{tenant.name} updated. New rent QAR {tenant.monthly_rent} applies from {tenant.rent_effective_from:%B %Y}; earlier months keep their rent.')
                else:
                    messages.success(request, f'{tenant.name} updated.')
                return redirect('tenancy:detail', pk=tenant.pk)
    else:
        form = TenantForm(initial=model_to_dict(tenant), editing=True)

    prefix = [('Villas', reverse('villas:villa_list'))] if not request.user.is_owner else [('Businesses', reverse('businesses:list')), (partition.villa.business.name, reverse('businesses:detail', args=[partition.villa.business.pk]))]
    breadcrumbs = prefix + [
        (partition.villa.name, reverse('villas:villa_detail', args=[partition.villa.pk])),
        (partition.name, reverse('villas:partition_detail', args=[partition.pk])),
        (tenant.name, reverse('tenancy:detail', args=[tenant.pk])),
        ('Edit', None),
    ]
    return render(request, 'components/form_page.html', {
        'form': form,
        'title': f'Edit Tenant — {tenant.name}',
        'submit_label': 'Save Changes',
        'cancel_url': reverse('tenancy:detail', args=[tenant.pk]),
        'breadcrumbs': breadcrumbs,
    })

@login_required
def tenant_create(request, partition_pk):
    partition = get_object_or_404(selectors.partitions_visible_to(request.user), pk=partition_pk)
    if not (request.user.is_owner or selectors.can_manage_villas_and_tenants(request.user)):
        raise PermissionDenied('You cannot move in a tenant here.')
    if partition.villa.is_sold or partition.status == 'archived' or partition.villa.is_archived or partition.villa.business.is_archived:
        if selectors.is_field_staff(request.user):
            return redirect('villas:villa_list')
        return redirect('villas:partition_detail', pk=partition.pk)
    initial = {}
    if partition.rent is not None:
        initial['monthly_rent'] = partition.rent
        initial['deposit'] = partition.rent

    is_staff = not request.user.is_owner

    if request.method == 'POST':
        form = TenantForm(request.POST, initial=initial, restrict_past_move_in=False, lock_rent=False, lock_deposit=is_staff)
        if form.is_valid():
            try:
                from django.db import transaction
                with transaction.atomic():
                    rent_paid = form.cleaned_data.pop('rent_paid', 0)
                    rent_method = form.cleaned_data.pop('rent_method', None) or form.cleaned_data.get('deposit_method', 'cash')
                    tenant = create_tenant(partition=partition, created_by=request.user, **form.cleaned_data)
                    if rent_paid:
                        from apps.billing.services import collect_rent
                        collect_rent(partition=partition, year=tenant.move_in_date.year, month=tenant.move_in_date.month, method=rent_method, collected_by=request.user, amount=rent_paid, collected_at=min(tenant.move_in_date, date.today()))
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f'{tenant.name} moved in to {partition.name}.')
                return redirect('tenancy:detail', pk=tenant.pk)
    else:
        form = TenantForm(initial=initial, restrict_past_move_in=False, lock_rent=False, lock_deposit=is_staff)
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Move In Tenant — {partition.name}', 'submit_label': 'Move In', 'cancel_url': reverse('villas:partition_detail', args=[partition.pk]), 'breadcrumbs': [('Businesses', reverse('businesses:list')), (partition.villa.business.name, reverse('businesses:detail', args=[partition.villa.business.pk])), (partition.villa.name, reverse('villas:villa_detail', args=[partition.villa.pk])), (partition.name, reverse('villas:partition_detail', args=[partition.pk])), ('Move In Tenant', None)]})

@login_required
def tenant_move_out(request, pk):
    tenant = get_object_or_404(selectors.tenants_visible_to(request.user), pk=pk)
    partition = tenant.partition
    if partition.villa.is_sold:
        raise PermissionDenied('Cannot move out tenants of a sold villa.')
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

@login_required
def tenant_deposit_edit(request, pk):
    tenant = get_object_or_404(selectors.tenants_visible_to(request.user), pk=pk)
    partition = tenant.partition
    if partition.villa.is_sold:
        raise PermissionDenied('Cannot edit deposit details for a sold villa.')
    if not (request.user.is_owner or selectors.can_manage_villa(request.user, partition.villa)):
        raise PermissionDenied('You cannot edit deposit details for this tenant.')

    default_back = reverse('villas:villa_detail', args=[partition.villa_id])
    if request.method == 'POST':
        form = DepositCollectForm(request.POST, tenant=tenant)
        if form.is_valid():
            data = form.cleaned_data
            try:
                if data['deposit'] != tenant.deposit:
                    update_tenant(tenant=tenant, updated_by=request.user, deposit=data['deposit'])
                if data['amount'] > 0:
                    collect_deposit(tenant=tenant, amount=data['amount'], method=data['method'], collected_by=request.user, collected_at=data['collected_at'])
            except DomainError as exc:
                messages.error(request, str(exc))
            else:
                tenant.refresh_from_db()
                messages.success(request, f'Advance for {tenant.name}: QAR {tenant.deposit_paid} received, QAR {tenant.deposit_pending} pending.' if tenant.deposit else f'{tenant.name}: no advance.')
        else:
            for error_list in form.errors.values():
                for error in error_list:
                    messages.error(request, error)
        return redirect(_safe_back(request, request.POST.get('next'), default_back))

    form = DepositCollectForm(tenant=tenant, initial={'deposit': tenant.deposit, 'amount': tenant.deposit_pending, 'collected_at': date.today()})
    return render(request, 'components/form_page.html', {
        'form': form,
        'title': f'Advance — {partition.name} ({tenant.name}): QAR {tenant.deposit_paid} received, QAR {tenant.deposit_pending} pending',
        'submit_label': 'Save Advance',
        'cancel_url': _safe_back(request, request.GET.get('next'), default_back),
    })


def _deposit_for_change(request, pk):
    collection = get_object_or_404(DepositCollection.all_objects.select_related('tenant__partition__villa'), pk=pk, tenant__in=selectors.tenants_visible_to(request.user))
    villa = collection.tenant.partition.villa
    if villa.is_sold:
        raise PermissionDenied('Cannot change advance collections of a sold villa.')
    if not (request.user.is_owner or selectors.can_manage_villa(request.user, villa)):
        raise PermissionDenied('You cannot change this advance collection.')
    return collection, reverse('tenancy:detail', args=[collection.tenant_id])

@login_required
def deposit_void(request, pk):
    collection, back = _deposit_for_change(request, pk)
    back = _safe_back(request, request.GET.get('next'), back)
    if collection.is_voided:
        messages.info(request, 'This advance collection is already cancelled.')
        return redirect(back)
    if request.method == 'POST':
        form = ReasonForm(request.POST)
        if form.is_valid():
            try:
                void_deposit(collection=collection, voided_by=request.user, reason=form.cleaned_data['reason'])
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f'Advance collection of QAR {collection.amount} cancelled. It is pending again.')
                return redirect(back)
    else:
        form = ReasonForm()
    return render(request, 'components/confirm_reason.html', {'form': form, 'title': f'Cancel advance collection of QAR {collection.amount}?', 'message': 'The record is kept and marked cancelled (never deleted). The amount is removed from income and becomes pending again.', 'submit_label': 'Cancel collection', 'cancel_url': back})

@login_required
def deposit_correct(request, pk):
    collection, back = _deposit_for_change(request, pk)
    back = _safe_back(request, request.GET.get('next'), back)
    if collection.is_voided:
        messages.info(request, 'A cancelled collection cannot be corrected.')
        return redirect(back)
    if request.method == 'POST':
        form = DepositCorrectionForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                correct_deposit(collection=collection, new_amount=d['amount'], voided_by=request.user, reason=d['reason'], method=d['method'], collected_at=d['collected_at'])
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f'Advance collection corrected to QAR {d["amount"]}. The original is kept as cancelled.')
                return redirect(back)
    else:
        form = DepositCorrectionForm(initial={'amount': collection.amount, 'method': collection.method, 'collected_at': collection.collected_at})
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Correct advance collection — {collection.tenant.name}', 'submit_label': 'Save correction', 'cancel_url': back})


def _safe_back(request, target, default):
    from django.utils.http import url_has_allowed_host_and_scheme
    return target if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()) else default

