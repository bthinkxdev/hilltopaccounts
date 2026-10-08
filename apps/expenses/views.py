from datetime import date
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from apps.accounts import selectors
from apps.shared.exceptions import DomainError
from apps.villas.models import Partition
from apps.shared.forms import ReasonForm
from apps.shared.pagination import paginate_queryset
from . import selectors as expense_selectors
from .forms import ExpenseForm, MarkPaidForm, RecurringExpenseForm, ReviseExpenseForm
from .models import Expense, PaidBy, RecurringExpense
from .selectors import DueBucket
from .services import (cancel_expense, can_pay_expense, create_expense, create_recurring_expense, deactivate_recurring_expense, get_or_create_category, mark_expense_paid,
                       reverse_expense, revise_unpaid_expense, update_recurring_expense, verify_expense)

def _can_manage_expense(user, expense):
    if expense.villa_id:
        return selectors.can_manage_villa(user, expense.villa)
    return selectors.is_business_manager_of(user, expense.business)

@login_required
def expense_list(request):
    today = date.today()
    visible = selectors.expenses_visible_to(request.user)
    if selectors.is_field_staff(request.user):
        visible = visible.filter(paid_by=PaidBy.STAFF)
    qs = visible.select_related('business', 'villa', 'partition', 'category')
    villa_id = request.GET.get('villa', '')
    if villa_id.isdigit():
        qs = qs.filter(villa_id=villa_id)
    status = request.GET.get('status', Expense.Status.ACTIVE)
    if status:
        qs = qs.filter(status=status)
    due = request.GET.get('due', '')
    if due in dict(DueBucket.CHOICES):
        qs = expense_selectors.filter_by_due_bucket(qs, due, today)
    summary_base = visible.filter(villa_id=villa_id) if villa_id.isdigit() else visible
    page_obj = paginate_queryset(request, qs.order_by('due_date', '-date', '-id') if due in (DueBucket.UNPAID, DueBucket.OVERDUE, DueBucket.TODAY, DueBucket.UPCOMING) else qs.order_by('-date', '-id'))
    scope = selectors.ManageScope(request.user)
    for expense in page_obj:
        expense.due_state = expense_selectors.due_state(expense, today)
        expense.can_manage = scope.can_manage_villa(expense.villa_id, expense.business_id) if expense.villa_id else scope.is_admin(expense.business_id)
        expense.can_pay = scope.can_pay_expense(expense)
        expense.can_verify = scope.can_verify_expense(expense)
        expense.can_edit = expense.can_manage and expense.status == 'active' and expense.paid_on is None
    villas = selectors.villas_visible_to(request.user).filter(is_archived=False).select_related('business')
    has_filters = bool(villa_id.isdigit() or (due in dict(DueBucket.CHOICES)) or ('status' in request.GET and status != Expense.Status.ACTIVE))
    context = {'page_obj': page_obj, 'status': status, 'due': due, 'due_choices': DueBucket.CHOICES, 'is_field_staff': selectors.is_field_staff(request.user), 'villas': villas, 'selected_villa': villa_id, 'has_filters': has_filters, 'summary': expense_selectors.due_summary(summary_base, today), 'can_add': selectors.manageable_villas(request.user).filter(is_archived=False).exists(), 'breadcrumbs': [('Expenses', None)]}
    return render(request, 'expenses/list.html', context)

def _expense_form_context(request, form, villa_pk, visible_expenses):
    cancel_url = reverse('villas:villa_detail', args=[villa_pk]) if villa_pk is not None else reverse('expenses:list')
    return {'form': form, 'title': 'Add Expense', 'cancel_url': cancel_url, 'expense_form': True, 'expense_names': expense_selectors.used_expense_names(visible_expenses), 'suggest_url': reverse('expenses:suggest')}

@login_required
def expense_create(request, villa_pk=None):
    manageable = selectors.manageable_villas(request.user)
    if not request.user.is_owner:
        manageable = manageable.filter(is_archived=False)
    if not manageable.exists():
        raise PermissionDenied('You are not authorized to add expenses.')
    preselected = request.GET.get('villa') or villa_pk
    if villa_pk is not None:
        get_object_or_404(manageable, pk=villa_pk)
    visible_expenses = selectors.expenses_visible_to(request.user)
    if request.method == 'POST':
        form = ExpenseForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            data = form.cleaned_data
            paid = data.pop('mark_paid')
            category = get_or_create_category(name=data.pop('name'))
            if paid:
                data['paid_on'] = data['date']
            else:
                data.pop('payment_method', None)
            try:
                expense = create_expense(category=category, created_by=request.user, **data)
            except PermissionDenied as exc:
                form.add_error('paid_by', str(exc))
                return render(request, 'components/form_page.html', _expense_form_context(request, form, villa_pk, visible_expenses))
            messages.success(request, f'{expense.category} — QAR {expense.amount} recorded for {expense.villa.name}.')
            return redirect('villas:villa_detail', pk=expense.villa_id) if villa_pk is not None else redirect('expenses:list')
    else:
        initial = {'villa': preselected, 'date': date.today()}
        partition_id = request.GET.get('partition', '')
        if partition_id.isdigit():
            partition = Partition.objects.filter(pk=partition_id, villa__in=manageable).first()
            if partition is not None:
                initial.update(villa=partition.villa_id, partition=partition.pk)
        initial['paid_by'] = 'owner' if request.user.is_owner else 'staff'
        form = ExpenseForm(user=request.user, initial=initial)
    return render(request, 'components/form_page.html', _expense_form_context(request, form, villa_pk, visible_expenses))

@login_required
@require_GET
def expense_suggest(request):
    """Latest amount used under an expense name, scoped to what this user may already see."""
    name = request.GET.get('name', '').strip()
    if not name:
        return JsonResponse({'amount': None})
    villa_id = request.GET.get('villa', '')
    villa = selectors.manageable_villas(request.user).filter(pk=villa_id).first() if villa_id.isdigit() else None
    amount = expense_selectors.latest_amount_for(selectors.expenses_visible_to(request.user), name=name, villa=villa)
    return JsonResponse({'amount': str(amount) if amount is not None else None})

@login_required
def expense_mark_paid(request, pk):
    expense = get_object_or_404(selectors.expenses_visible_to(request.user).select_related('villa', 'business', 'category'), pk=pk)
    if not can_pay_expense(request.user, expense):
        raise PermissionDenied('You cannot settle this expense.')
    if request.method == 'POST':
        form = MarkPaidForm(request.POST)
        if form.is_valid():
            try:
                mark_expense_paid(expense=expense, marked_by=request.user, **form.cleaned_data)
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f'{expense.category} marked as paid.')
                return redirect('expenses:list')
    else:
        form = MarkPaidForm(initial={'paid_on': date.today(), 'payment_method': expense.payment_method or Expense.Method.CASH})
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Mark paid — {expense.category} (QAR {expense.amount})', 'submit_label': 'Mark Paid', 'cancel_url': reverse('expenses:list')})

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


@login_required
def expense_revise(request, pk):
    expense = get_object_or_404(selectors.expenses_visible_to(request.user).select_related('villa', 'business', 'category'), pk=pk)
    if not _can_manage_expense(request.user, expense):
        raise PermissionDenied('You cannot edit this expense.')
    back = reverse('villas:villa_detail', args=[expense.villa_id]) if expense.villa_id else reverse('expenses:list')
    if request.method == 'POST':
        form = ReviseExpenseForm(request.POST)
        if form.is_valid():
            try:
                revise_unpaid_expense(expense=expense, updated_by=request.user, **form.cleaned_data)
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f'{expense.category} updated to QAR {form.cleaned_data["amount"]}.')
                return redirect(back)
    else:
        form = ReviseExpenseForm(initial={'amount': expense.amount, 'due_date': expense.due_date, 'description': expense.description})
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Edit {expense.category} — {expense.villa.name if expense.villa_id else expense.business.name}', 'cancel_url': back})

def _villa_for_fixed_expenses(request, villa_pk):
    villa = get_object_or_404(selectors.villas_visible_to(request.user).select_related('business'), pk=villa_pk)
    if not (request.user.is_owner or selectors.is_business_manager_of(request.user, villa.business)):
        raise PermissionDenied('Only the Owner or the Business Manager can set fixed expenses.')
    return villa

@login_required
def recurring_create(request, villa_pk):
    villa = _villa_for_fixed_expenses(request, villa_pk)
    back = reverse('villas:villa_detail', args=[villa.pk]) + '#fixed-expenses'
    if request.method == 'POST':
        form = RecurringExpenseForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            category = get_or_create_category(name=data['name'])
            if RecurringExpense.objects.filter(villa=villa, category=category).exists():
                form.add_error('name', f'{category.name} is already a fixed expense of this villa — edit it instead.')
            else:
                create_recurring_expense(villa=villa, category=category, amount=data['amount'], due_day=data['due_day'], paid_by=data['paid_by'], auto_debit=data['auto_debit'], created_by=request.user)
                messages.success(request, f'Fixed expense “{category.name}” added to {villa.name}.')
                return redirect(back)
    else:
        form = RecurringExpenseForm()
    names = expense_selectors.used_expense_names(selectors.expenses_visible_to(request.user))
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Add fixed monthly expense — {villa.name}', 'cancel_url': back, 'expense_names': names, 'datalist_only': True})

@login_required
def recurring_edit(request, pk):
    recurring = get_object_or_404(RecurringExpense.objects.select_related('villa', 'villa__business', 'category'), pk=pk, villa__in=selectors.villas_visible_to(request.user))
    villa = _villa_for_fixed_expenses(request, recurring.villa_id)
    back = reverse('villas:villa_detail', args=[villa.pk]) + '#fixed-expenses'
    if request.method == 'POST':
        form = RecurringExpenseForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            category = get_or_create_category(name=data['name'])
            clash = RecurringExpense.objects.filter(villa=villa, category=category).exclude(pk=recurring.pk).exists()
            if clash:
                form.add_error('name', f'{category.name} is already a fixed expense of this villa.')
            else:
                update_recurring_expense(recurring=recurring, updated_by=request.user, category=category, amount=data['amount'], due_day=data['due_day'], paid_by=data['paid_by'], auto_debit=data['auto_debit'])
                messages.success(request, f'“{category.name}” updated — it applies from the next month generated.')
                return redirect(back)
    else:
        form = RecurringExpenseForm(initial={'name': recurring.category.name, 'amount': recurring.amount, 'due_day': recurring.due_day, 'paid_by': recurring.paid_by, 'auto_debit': recurring.auto_debit})
    names = expense_selectors.used_expense_names(selectors.expenses_visible_to(request.user))
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Edit fixed expense — {recurring.category.name}', 'cancel_url': back, 'expense_names': names, 'datalist_only': True})

@login_required
def recurring_stop(request, pk):
    recurring = get_object_or_404(RecurringExpense.objects.select_related('villa', 'villa__business', 'category'), pk=pk, is_active=True, villa__in=selectors.villas_visible_to(request.user))
    villa = _villa_for_fixed_expenses(request, recurring.villa_id)
    back = reverse('villas:villa_detail', args=[villa.pk]) + '#fixed-expenses'
    if request.method == 'POST':
        form = ReasonForm(request.POST)
        if form.is_valid():
            deactivate_recurring_expense(recurring=recurring, updated_by=request.user, reason=form.cleaned_data['reason'])
            messages.success(request, f'“{recurring.category.name}” will no longer be generated each month.')
            return redirect(back)
    else:
        form = ReasonForm()
    return render(request, 'components/confirm_reason.html', {'form': form, 'title': f'Stop {recurring.category.name}?', 'message': 'No new monthly expense will be created. Expenses already generated stay on record.', 'cancel_url': back})


def _back_to(request, default):
    """Where to return after a one-tap action — only ever a path on this site."""
    from django.utils.http import url_has_allowed_host_and_scheme
    target = request.POST.get('next', '')
    return target if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()) else default

@login_required
@require_POST
def expense_quick_paid(request, pk):
    """One tap: 'Paid'. Staff-paid money awaits the owner's verification; the owner's own payment is final."""
    expense = get_object_or_404(selectors.expenses_visible_to(request.user).select_related('villa', 'business', 'category'), pk=pk)
    if not can_pay_expense(request.user, expense):
        raise PermissionDenied('You cannot settle this expense.')
    is_admin = request.user.is_owner or selectors.is_business_manager_of(request.user, expense.business)
    try:
        mark_expense_paid(expense=expense, paid_on=date.today(), payment_method=Expense.Method.BANK_TRANSFER if is_admin else Expense.Method.CASH, marked_by=request.user)
    except DomainError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f'{expense.category} marked paid.' if is_admin else f'{expense.category} marked paid — the owner will verify it.')
    return redirect(_back_to(request, reverse('expenses:list')))

@login_required
@require_POST
def expense_verify(request, pk):
    """One tap: the Owner / Business Manager confirms a staff-paid expense as received."""
    expense = get_object_or_404(selectors.expenses_visible_to(request.user).select_related('villa', 'business', 'category'), pk=pk)
    try:
        verify_expense(expense=expense, verified_by=request.user)
    except DomainError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f'{expense.category} verified.')
    return redirect(_back_to(request, reverse('expenses:list')))
