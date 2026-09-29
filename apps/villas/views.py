from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from apps.accounts import selectors
from apps.businesses.forms import ArchiveReasonForm
from apps.businesses.models import Business
from apps.shared.pagination import paginate_queryset
from .forms import PartitionForm, VillaForm
from .services import archive_partition, archive_villa, create_partition, create_villa

@login_required
def villa_list(request):
    qs = selectors.villas_visible_to(request.user).filter(is_archived=False).select_related('business')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(name__icontains=q)
    business_id = request.GET.get('business')
    if business_id:
        qs = qs.filter(business_id=business_id)
    page_obj = paginate_queryset(request, qs)
    return render(request, 'villas/villa_list.html', {'page_obj': page_obj, 'q': q, 'businesses': selectors.businesses_visible_to(request.user).filter(is_archived=False), 'selected_business': business_id, 'can_create_villa': selectors.businesses_managed_by(request.user).exists(), 'breadcrumbs': [('Villas', None)]})

@login_required
def villa_detail(request, pk):
    villa = get_object_or_404(selectors.villas_visible_to(request.user), pk=pk)
    partitions = selectors.partitions_visible_to(request.user).filter(villa=villa).select_related().order_by('name')
    context = {'villa': villa, 'partitions': partitions, 'occupied_count': partitions.filter(tenancies__status='active').distinct().count(), 'can_manage': request.user.is_owner or selectors.is_business_manager_of(request.user, villa.business) or selectors.is_villa_staff_of(request.user, villa), 'can_view_financials': selectors.can_view_financial_kpis(request.user), 'breadcrumbs': [('Businesses', reverse('businesses:list')), (villa.business.name, reverse('businesses:detail', args=[villa.business.pk])), (villa.name, None)]}
    context['vacant_count'] = partitions.count() - context['occupied_count']
    if context['can_view_financials']:
        from apps.billing import selectors as billing_selectors
        from apps.expenses import selectors as expenses_selectors
        invoices = selectors.invoices_visible_to(request.user).filter(partition__villa=villa)
        expenses = selectors.expenses_visible_to(request.user).filter(villa=villa)
        context.update(expected_revenue=billing_selectors.expected_revenue(invoices), collected=billing_selectors.collected_amount(invoices), outstanding=billing_selectors.outstanding_amount(invoices), total_expenses=expenses_selectors.valid_expense_total(expenses), net_profit=billing_selectors.profit(invoices, expenses))
    return render(request, 'villas/villa_detail.html', context)

@login_required
def villa_create(request):
    manageable_businesses = selectors.businesses_managed_by(request.user)
    if not manageable_businesses.exists():
        raise PermissionDenied("You don't manage any business yet — ask the Owner to assign you one.")
    business_id = request.GET.get('business') or request.POST.get('business')
    initial = {}
    if business_id:
        preselected = get_object_or_404(manageable_businesses, pk=business_id)
        initial['business'] = preselected.pk
    if request.method == 'POST':
        form = VillaForm(request.POST, business_queryset=manageable_businesses)
        if form.is_valid():
            villa = create_villa(business=form.cleaned_data.pop('business'), created_by=request.user, **form.cleaned_data)
            messages.success(request, f'Villa “{villa.name}” created.')
            return redirect('villas:villa_detail', pk=villa.pk)
    else:
        form = VillaForm(business_queryset=manageable_businesses, initial=initial)
    return render(request, 'components/form_page.html', {'form': form, 'title': 'Add Villa', 'cancel_url': reverse('villas:villa_list'), 'breadcrumbs': [('Villas', reverse('villas:villa_list')), ('New Villa', None)]})

@login_required
def villa_archive(request, pk):
    villa = get_object_or_404(selectors.villas_visible_to(request.user), pk=pk)
    if not (request.user.is_owner or selectors.is_business_manager_of(request.user, villa.business)):
        raise PermissionDenied('You cannot archive this villa.')
    if request.method == 'POST':
        form = ArchiveReasonForm(request.POST)
        if form.is_valid():
            archive_villa(villa=villa, archived_by=request.user, reason=form.cleaned_data['reason'])
            messages.success(request, f'Villa “{villa.name}” archived.')
            return redirect('businesses:detail', pk=villa.business.pk)
    else:
        form = ArchiveReasonForm()
    return render(request, 'components/confirm_reason.html', {'form': form, 'title': f'Archive {villa.name}?', 'message': 'This villa will be archived, not deleted — its history stays intact and visible.', 'cancel_url': reverse('villas:villa_detail', args=[villa.pk])})

@login_required
def partition_list(request):
    qs = selectors.partitions_visible_to(request.user).select_related('villa', 'villa__business')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(name__icontains=q)
    villa_id = request.GET.get('villa')
    if villa_id:
        qs = qs.filter(villa_id=villa_id)
    status = request.GET.get('occupancy')
    if status in ('occupied', 'vacant'):
        occupied_ids = qs.filter(tenancies__status='active').values_list('pk', flat=True).distinct()
        qs = qs.filter(pk__in=occupied_ids) if status == 'occupied' else qs.exclude(pk__in=occupied_ids)
    page_obj = paginate_queryset(request, qs.order_by('villa__name', 'name'))
    for partition in page_obj:
        partition.occupied = partition.is_occupied
    return render(request, 'villas/partition_list.html', {'page_obj': page_obj, 'q': q, 'villas': selectors.villas_visible_to(request.user).filter(is_archived=False), 'selected_villa': villa_id, 'selected_occupancy': status, 'can_create_partition': (request.user.is_owner or selectors.can_manage_villas_and_tenants(request.user)) and selectors.villas_visible_to(request.user).filter(is_archived=False).exists(), 'breadcrumbs': [('Partitions', None)]})

@login_required
def partition_detail(request, pk):
    partition = get_object_or_404(selectors.partitions_visible_to(request.user), pk=pk)
    from apps.billing import selectors as billing_selectors
    from apps.billing.models import Charge
    invoices = selectors.invoices_visible_to(request.user).filter(partition=partition).order_by('-issue_date')
    for invoice in invoices:
        invoice.computed_status = billing_selectors.invoice_status(invoice)
        invoice.computed_outstanding = billing_selectors.invoice_outstanding(invoice)
    return render(request, 'villas/partition_detail.html', {'partition': partition, 'current_tenant': partition.current_tenant, 'charges': Charge.objects.filter(partition=partition, is_active=True), 'invoices': invoices[:20], 'can_manage': request.user.is_owner or selectors.can_manage_villas_and_tenants(request.user), 'breadcrumbs': [('Businesses', reverse('businesses:list')), (partition.villa.business.name, reverse('businesses:detail', args=[partition.villa.business.pk])), (partition.villa.name, reverse('villas:villa_detail', args=[partition.villa.pk])), (partition.name, None)]})

@login_required
def partition_create(request, villa_pk=None):
    if not (request.user.is_owner or selectors.can_manage_villas_and_tenants(request.user)):
        raise PermissionDenied('You cannot add partitions.')
    manageable_villas = selectors.villas_visible_to(request.user).filter(is_archived=False)
    if not manageable_villas.exists():
        raise PermissionDenied("You don't manage any villa yet.")
    villa = get_object_or_404(manageable_villas, pk=villa_pk) if villa_pk is not None else None
    if request.method == 'POST':
        form = PartitionForm(request.POST, villa_queryset=manageable_villas)
        if villa is not None:
            form.fields.pop('villa')
        if form.is_valid():
            partition = create_partition(villa=villa or form.cleaned_data.pop('villa'), created_by=request.user, **form.cleaned_data)
            messages.success(request, f'Partition “{partition.name}” created.')
            return redirect('villas:partition_detail', pk=partition.pk)
    else:
        form = PartitionForm(villa_queryset=manageable_villas)
        if villa is not None:
            form.fields.pop('villa')
    cancel_url = reverse('villas:villa_detail', args=[villa.pk]) if villa else reverse('villas:partition_list')
    breadcrumbs = [('Partitions', reverse('villas:partition_list')), ('New Partition', None)]
    if villa:
        breadcrumbs = [('Businesses', reverse('businesses:list')), (villa.business.name, reverse('businesses:detail', args=[villa.business.pk])), (villa.name, reverse('villas:villa_detail', args=[villa.pk])), ('New Partition', None)]
    return render(request, 'components/form_page.html', {'form': form, 'title': 'Add Partition', 'cancel_url': cancel_url, 'breadcrumbs': breadcrumbs})

@login_required
def partition_archive(request, pk):
    partition = get_object_or_404(selectors.partitions_visible_to(request.user), pk=pk)
    if not (request.user.is_owner or selectors.can_manage_villas_and_tenants(request.user)):
        raise PermissionDenied('You cannot archive this partition.')
    if request.method == 'POST':
        form = ArchiveReasonForm(request.POST)
        if form.is_valid():
            archive_partition(partition=partition, archived_by=request.user, reason=form.cleaned_data['reason'])
            messages.success(request, f'Partition “{partition.name}” archived.')
            return redirect('villas:villa_detail', pk=partition.villa.pk)
    else:
        form = ArchiveReasonForm()
    return render(request, 'components/confirm_reason.html', {'form': form, 'title': f'Archive {partition.name}?', 'message': 'This partition will be archived, not deleted.', 'cancel_url': reverse('villas:partition_detail', args=[partition.pk])})
