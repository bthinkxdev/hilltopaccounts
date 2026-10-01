from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from apps.accounts import selectors
from apps.billing import selectors as billing_selectors
from apps.expenses import selectors as expenses_selectors
from apps.shared.pagination import paginate_queryset
from apps.shared.periods import period_context
from .forms import ArchiveReasonForm, BusinessForm
from .services import archive_business, create_business

@login_required
def business_list(request):
    qs = selectors.businesses_visible_to(request.user).filter(is_archived=False)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(name__icontains=q)
    page_obj = paginate_queryset(request, qs)
    for business in page_obj:
        business.scoped_villa_count = selectors.villas_visible_to(request.user).filter(business=business).count()
    return render(request, 'businesses/list.html', {'page_obj': page_obj, 'q': q, 'breadcrumbs': [('Businesses', None)]})

@login_required
def business_detail(request, pk):
    business = get_object_or_404(selectors.businesses_visible_to(request.user), pk=pk)
    villas = selectors.villas_visible_to(request.user).filter(business=business, is_archived=False)
    period = period_context(request)
    financial = selectors.financial_villas(request.user).filter(business=business)
    context = {'period_pnl': billing_selectors.period_profit_loss(selectors.invoices_visible_to(request.user).filter(partition__villa__in=financial), selectors.expenses_visible_to(request.user).filter(villa__in=financial), period['period_start'], period['period_end']) if financial.exists() else None, 'keep': [], **period, 'business': business, 'villas': villas, 'can_view_financials': selectors.can_view_financial_kpis(request.user), 'breadcrumbs': [('Businesses', reverse('businesses:list')), (business.name, None)]}
    if context['can_view_financials']:
        invoices = selectors.invoices_visible_to(request.user).filter(partition__villa__business=business)
        expenses = selectors.expenses_visible_to(request.user).filter(business=business)
        context.update(expected_revenue=billing_selectors.expected_revenue(invoices), collected=billing_selectors.collected_amount(invoices), outstanding=billing_selectors.outstanding_amount(invoices), total_expenses=expenses_selectors.valid_expense_total(expenses), net_profit=billing_selectors.profit(invoices, expenses))
    return render(request, 'businesses/detail.html', context)

@login_required
def business_create(request):
    if not request.user.is_owner:
        raise PermissionDenied('Only the Owner can create a business.')
    if request.method == 'POST':
        form = BusinessForm(request.POST)
        if form.is_valid():
            business = create_business(name=form.cleaned_data['name'], description=form.cleaned_data['description'], created_by=request.user)
            messages.success(request, f'Business “{business.name}” created.')
            return redirect('businesses:detail', pk=business.pk)
    else:
        form = BusinessForm()
    return render(request, 'components/form_page.html', {'form': form, 'title': 'Add Business', 'cancel_url': reverse('businesses:list'), 'breadcrumbs': [('Businesses', reverse('businesses:list')), ('New', None)]})

@login_required
def business_archive(request, pk):
    business = get_object_or_404(selectors.businesses_visible_to(request.user), pk=pk)
    if not request.user.is_owner:
        raise PermissionDenied('Only the Owner can archive a business.')
    if request.method == 'POST':
        form = ArchiveReasonForm(request.POST)
        if form.is_valid():
            archive_business(business=business, archived_by=request.user, reason=form.cleaned_data['reason'])
            messages.success(request, f'Business “{business.name}” archived.')
            return redirect('businesses:list')
    else:
        form = ArchiveReasonForm()
    return render(request, 'components/confirm_reason.html', {'form': form, 'title': f'Archive {business.name}?', 'message': 'This business will be archived, not deleted — its history stays intact and visible.', 'cancel_url': reverse('businesses:detail', args=[business.pk]), 'breadcrumbs': [('Businesses', reverse('businesses:list')), (business.name, reverse('businesses:detail', args=[business.pk])), ('Archive', None)]})
