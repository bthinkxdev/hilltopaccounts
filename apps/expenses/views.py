from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from apps.accounts import selectors
from apps.shared.forms import ReasonForm
from apps.shared.pagination import paginate_queryset
from .forms import ExpenseForm
from .models import Expense
from .services import cancel_expense, create_expense, reverse_expense

@login_required
def expense_list(request):
    qs = selectors.expenses_visible_to(request.user).select_related('business', 'villa', 'category')
    business_id = request.GET.get('business')
    if business_id:
        qs = qs.filter(business_id=business_id)
    status = request.GET.get('status', Expense.Status.ACTIVE)
    if status:
        qs = qs.filter(status=status)
    page_obj = paginate_queryset(request, qs.order_by('-date'))
    return render(request, 'expenses/list.html', {'page_obj': page_obj, 'status': status, 'businesses': selectors.businesses_visible_to(request.user).filter(is_archived=False), 'selected_business': business_id, 'breadcrumbs': [('Expenses', None)]})

@login_required
def expense_create(request):
    business_id = request.GET.get('business') or request.POST.get('business_id')
    business = get_object_or_404(selectors.businesses_visible_to(request.user), pk=business_id) if business_id else None
    if business is None:
        raise PermissionDenied('Choose a business to add an expense to.')
    if not (request.user.is_owner or selectors.can_manage_villas_and_tenants(request.user)):
        raise PermissionDenied('You are not authorized to add expenses.')
    if request.method == 'POST':
        form = ExpenseForm(request.POST, request.FILES, business=business, user=request.user)
        if form.is_valid():
            villa = form.cleaned_data.pop('villa', None)
            if villa is None and (not (request.user.is_owner or selectors.is_business_manager_of(request.user, business))):
                form.add_error(None, 'Only the Owner or a Business Manager can add a business-wide expense.')
            else:
                expense = create_expense(business=business, villa=villa, created_by=request.user, **form.cleaned_data)
                messages.success(request, f'Expense of QAR {expense.amount} recorded.')
                return redirect('expenses:list')
    else:
        form = ExpenseForm(business=business, user=request.user)
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Add Expense — {business.name}', 'cancel_url': reverse('expenses:list')})

@login_required
def expense_cancel(request, pk):
    expense = get_object_or_404(selectors.expenses_visible_to(request.user), pk=pk)
    if not (request.user.is_owner or selectors.is_business_manager_of(request.user, expense.business)):
        raise PermissionDenied('You cannot cancel this expense.')
    if request.method == 'POST':
        form = ReasonForm(request.POST)
        if form.is_valid():
            cancel_expense(expense=expense, cancelled_by=request.user, reason=form.cleaned_data['reason'])
            messages.success(request, 'Expense cancelled.')
            return redirect('expenses:list')
    else:
        form = ReasonForm()
    return render(request, 'components/confirm_reason.html', {'form': form, 'title': 'Cancel this expense?', 'message': "For an expense that hasn't been relied on yet — it stays on record, excluded from P&L.", 'cancel_url': reverse('expenses:list')})

@login_required
def expense_reverse(request, pk):
    expense = get_object_or_404(selectors.expenses_visible_to(request.user), pk=pk)
    if not (request.user.is_owner or selectors.is_business_manager_of(request.user, expense.business)):
        raise PermissionDenied('You cannot reverse this expense.')
    if request.method == 'POST':
        form = ReasonForm(request.POST)
        if form.is_valid():
            reverse_expense(expense=expense, reversed_by=request.user, reason=form.cleaned_data['reason'])
            messages.success(request, 'Expense reversed.')
            return redirect('expenses:list')
    else:
        form = ReasonForm()
    return render(request, 'components/confirm_reason.html', {'form': form, 'title': 'Reverse this expense?', 'message': 'For an expense that already fed into a reported P&L figure — it stays on record, excluded from future totals.', 'cancel_url': reverse('expenses:list')})
