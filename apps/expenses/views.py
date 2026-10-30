from datetime import date
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from apps.accounts import selectors
from apps.shared.exceptions import DomainError
from apps.villas.models import Partition, Villa
from apps.shared.forms import ReasonForm
from apps.shared.pagination import paginate_queryset
from apps.shared.periods import period_context
from . import selectors as expense_selectors
from .forms import ExpenseForm, MarkPaidForm, RecurringExpenseForm, ReviseExpenseForm
from .models import Expense, PaidBy, RecurringExpense
from .selectors import DueBucket
from .services import (cancel_expense, can_pay_expense, create_expense, create_recurring_expense, deactivate_recurring_expense, get_or_create_category, mark_expense_paid,
                       reverse_expense, revise_unpaid_expense, update_recurring_expense, verify_expense)

def _can_manage_expense(user, expense):
    if expense.villa_id:
        if expense.villa.is_sold:
            return False
        return selectors.can_manage_villa(user, expense.villa)
    return selectors.is_business_manager_of(user, expense.business)

@login_required
def expense_list(request):
    today = date.today()
    raw_month = request.GET.get('month', '')
    if '-' in str(raw_month):
        try:
            y, m = str(raw_month).split('-')
            get_params = request.GET.copy()
            get_params['year'] = y
            get_params['month'] = str(int(m))
            get_params['period'] = 'month'
            request.GET = get_params
        except Exception:
            pass

    period = period_context(request, today, allow_future=True)
    if request.GET.get('period') == 'all':
        # All time: e.g. the dashboard's "Overdue expenses" count covers every month, so its list must too.
        period.update(period='all', period_label='All time', period_start=date(2000, 1, 1), period_end=date(today.year + 10, 12, 31))
    period['allow_all_period'] = True
    start, end = period['period_start'], period['period_end']

    visible = selectors.expenses_visible_to(request.user)
    qs = visible.select_related('business', 'villa', 'partition', 'category', 'paid_by_user').filter(date__gte=start, date__lte=end)
    villa_id = request.GET.get('villa', '')
    if villa_id.isdigit():
        qs = qs.filter(villa_id=villa_id)
    status = request.GET.get('status', Expense.Status.ACTIVE)
    if status:
        qs = qs.filter(status=status)
    due = request.GET.get('due', '')
    if due in dict(DueBucket.CHOICES):
        qs = expense_selectors.filter_by_due_bucket(qs, due, today)
    staff_id = request.GET.get('staff', '').strip() or request.GET.get('paid_by', '').strip()
    if staff_id.isdigit():
        uid = int(staff_id)
        qs = qs.filter(Q(paid_by_user_id=uid) | Q(created_by_id=uid))
    elif staff_id in (PaidBy.STAFF, PaidBy.OWNER):
        qs = qs.filter(paid_by=staff_id)
    payable = request.GET.get('payable') == '1'
    if payable:
        # Bills staff may pay from collected cash (the dashboard's "Staff bills due"): not auto-debits, not villa rent.
        qs = qs.exclude(recurring__auto_debit=True).exclude(category__name__iregex=selectors.LANDLORD_RENT_REGEX)

    summary_base = visible.filter(date__gte=start, date__lte=end)
    if villa_id.isdigit():
        summary_base = summary_base.filter(villa_id=villa_id)
    if staff_id.isdigit():
        summary_base = summary_base.filter(Q(paid_by_user_id=int(staff_id)) | Q(created_by_id=int(staff_id)))
    elif staff_id in (PaidBy.STAFF, PaidBy.OWNER):
        summary_base = summary_base.filter(paid_by=staff_id)

    page_obj = paginate_queryset(request, qs.order_by('due_date', '-created_at', '-id') if due in (DueBucket.UNPAID, DueBucket.OVERDUE, DueBucket.TODAY, DueBucket.UPCOMING) else qs.order_by('-created_at', '-id'))
    scope = selectors.ManageScope(request.user)
    is_field_staff = selectors.is_field_staff(request.user)
    for expense in page_obj:
        expense.due_state = expense_selectors.due_state(expense, today)
        expense.can_manage = scope.can_manage_villa(expense.villa_id, expense.business_id) if expense.villa_id else scope.is_admin(expense.business_id)
        expense.can_pay = scope.can_pay_expense(expense)
        expense.can_verify = scope.can_verify_expense(expense)
        expense.can_edit = expense.can_manage and expense.status == 'active' and expense.paid_on is None and not (is_field_staff and expense.paid_by == PaidBy.OWNER)

    User = get_user_model()
    if request.user.is_owner:
        staff_users = list(User.objects.filter(is_active=True, is_owner=False).exclude(username='system').order_by('first_name', 'username'))
    else:
        staff_users = [request.user]

    villas = selectors.villas_visible_to(request.user).filter(is_archived=False, is_sold=False).select_related('business')
    has_filters = bool(villa_id.isdigit() or (due in dict(DueBucket.CHOICES)) or ('status' in request.GET and status != Expense.Status.ACTIVE) or staff_id or payable)

    keep = []
    if villa_id.isdigit():
        keep.append(('villa', villa_id))
    if due in dict(DueBucket.CHOICES):
        keep.append(('due', due))
    if status and status != Expense.Status.ACTIVE:
        keep.append(('status', status))
    if staff_id:
        keep.append(('staff', staff_id))
    if payable:
        keep.append(('payable', '1'))

    context = {
        'page_obj': page_obj,
        'status': status,
        'due': due,
        'paid_by': staff_id,
        'selected_staff': staff_id,
        'staff_users': staff_users,
        'due_choices': DueBucket.FILTER_CHOICES,
        'is_field_staff': is_field_staff,
        'villas': villas,
        'selected_villa': villa_id,
        'has_filters': has_filters,
        'summary': expense_selectors.due_summary(summary_base, today),
        'can_add': selectors.manageable_villas(request.user).filter(is_archived=False, is_sold=False).exists(),
        'breadcrumbs': [('Expenses', None)],
        'keep': keep,
        **period,
    }
    return render(request, 'expenses/list.html', context)

def _expense_form_context(request, form, villa_pk, visible_expenses):
    partition = request.GET.get('partition')
    cancel_url = reverse('villas:partition_detail', args=[partition]) if partition else (reverse('villas:villa_detail', args=[villa_pk]) if villa_pk else reverse('expenses:list'))
    return {'form': form, 'title': 'Add Expense', 'cancel_url': cancel_url, 'expense_form': True, 'expense_names': expense_selectors.used_expense_names(visible_expenses), 'suggest_url': reverse('expenses:suggest')}

def _create_monthly(request, form, data, villa_pk, visible_expenses):
    """The 'Monthly repeated' choice of the add-expense form: sets up a fixed monthly expense of the villa, starting
    with the month of the chosen date; earlier months are never touched."""
    villa, start = data['villa'], data['date']
    category = get_or_create_category(name=data['name'])
    if RecurringExpense.objects.filter(villa=villa, category=category).exists():
        form.add_error('name', f'{category.name} is already a monthly expense of this villa — edit it on the villa page instead.')
    else:
        try:
            create_recurring_expense(villa=villa, category=category, amount=data['amount'], due_day=min(start.day, 28), auto_debit=data.get('auto_debit', False), start_period=start, created_by=request.user)
        except (PermissionDenied, DomainError) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f'Monthly expense “{category.name}” added to {villa.name} from {start:%B %Y}.')
            return redirect(reverse('villas:villa_detail', args=[villa.pk]) + f'?period=month&year={start.year}&month={start.month}#fixed-expenses')
    return render(request, 'components/form_page.html', _expense_form_context(request, form, villa_pk, visible_expenses))

@login_required
def expense_create(request, villa_pk=None):
    if villa_pk is not None:
        villa = get_object_or_404(selectors.villas_visible_to(request.user), pk=villa_pk)
        if villa.is_sold:
            messages.info(request, 'This villa has been sold. No expenses can be added.')
            return redirect('villas:villa_detail', pk=villa.pk)
        if selectors.is_field_staff(request.user) and (villa.is_archived or villa.business.is_archived):
            messages.info(request, 'This villa has been archived.')
            return redirect('villas:villa_list')
    partition_id = request.GET.get('partition')
    if partition_id and partition_id.isdigit():
        partition = Partition.objects.filter(pk=partition_id).select_related('villa').first()
        if partition and partition.villa.is_sold:
            messages.info(request, 'This villa has been sold. No expenses can be added.')
            return redirect('villas:villa_detail', pk=partition.villa.pk)
    manageable = selectors.manageable_villas(request.user).filter(is_sold=False)
    if not request.user.is_owner:
        manageable = manageable.filter(is_archived=False)
    if not manageable.exists():
        if selectors.is_field_staff(request.user) and Villa.objects.filter(staff_assignments__user=request.user).exists():
            messages.info(request, 'This villa has been archived.')
            return redirect('villas:villa_list')
        raise PermissionDenied('You are not authorized to add expenses.')
    preselected = request.GET.get('villa') or villa_pk
    visible_expenses = selectors.expenses_visible_to(request.user)
    if request.method == 'POST':
        form = ExpenseForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            data = form.cleaned_data
            if data.pop('frequency') == 'monthly':
                return _create_monthly(request, form, data, villa_pk, visible_expenses)
            data.pop('auto_debit', None)
            paid = data.pop('mark_paid')
            category = get_or_create_category(name=data.pop('name'))
            if paid:
                data['paid_on'] = data['date']
            else:
                data.pop('payment_method', None)
            try:
                expense = create_expense(category=category, created_by=request.user, **data)
            except PermissionDenied as exc:
                form.add_error(None, str(exc))
                return render(request, 'components/form_page.html', _expense_form_context(request, form, villa_pk, visible_expenses))
            messages.success(request, f'{expense.category} — QAR {expense.amount} recorded for {expense.villa.name}.')
            if expense.partition_id:
                return redirect('villas:partition_detail', pk=expense.partition_id)
            return redirect('villas:villa_detail', pk=expense.villa_id) if (villa_pk or request.GET.get('villa')) else redirect('expenses:list')
    else:
        from apps.shared.periods import selected_year_month
        sel_year, sel_month = selected_year_month(request)
        initial = {'frequency': 'monthly' if request.GET.get('type') == 'monthly' else 'one_time', 'villa': preselected, 'date': date.today() if (sel_year, sel_month) == (date.today().year, date.today().month) else date(sel_year, sel_month, 1)}
        partition_id = request.GET.get('partition', '')
        if partition_id.isdigit():
            partition = Partition.objects.filter(pk=partition_id, villa__in=manageable).first()
            if partition is not None:
                initial.update(villa=partition.villa_id, partition=partition.pk)
            elif selectors.is_field_staff(request.user) and Partition.objects.filter(villa__staff_assignments__user=request.user, pk=partition_id).exists():
                messages.info(request, 'This villa has been archived.')
                return redirect('villas:villa_list')
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
    expense = get_object_or_404(selectors.expenses_visible_to(request.user).select_related('villa', 'business', 'category', 'recurring'), pk=pk)
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
    if not _can_manage_expense(request.user, expense) or (selectors.is_field_staff(request.user) and expense.paid_by == PaidBy.OWNER):
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
        form = ReviseExpenseForm(initial={'amount': expense.amount, 'description': expense.description})
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Edit {expense.category} — {expense.villa.name if expense.villa_id else expense.business.name}', 'cancel_url': back})

def _villa_for_fixed_expenses(request, villa_pk):
    villa = get_object_or_404(selectors.villas_visible_to(request.user).select_related('business'), pk=villa_pk)
    if villa.is_sold:
        raise PermissionDenied('This villa has been sold. Fixed expenses cannot be configured.')
    if not (request.user.is_owner or selectors.is_business_manager_of(request.user, villa.business) or selectors.is_villa_staff_of(request.user, villa)):
        raise PermissionDenied('Only the Admin, Business Manager, or assigned Villa Staff can set fixed expenses.')
    return villa

@login_required
def recurring_create(request, villa_pk):
    villa = _villa_for_fixed_expenses(request, villa_pk)
    back = reverse('villas:villa_detail', args=[villa.pk]) + '#fixed-expenses'
    can_auto_debit = selectors.is_business_manager_of(request.user, villa.business)
    if request.method == 'POST':
        form = RecurringExpenseForm(request.POST, can_auto_debit=can_auto_debit)
        if form.is_valid():
            data = form.cleaned_data
            category = get_or_create_category(name=data['name'])
            if RecurringExpense.objects.filter(villa=villa, category=category).exists():
                form.add_error('name', f'{category.name} is already a fixed expense of this villa — edit it instead.')
            else:
                create_recurring_expense(villa=villa, category=category, amount=data['amount'], due_day=data['due_day'], auto_debit=data.get('auto_debit', False), created_by=request.user)
                messages.success(request, f'Fixed expense “{category.name}” added to {villa.name}.')
                return redirect(back)
    else:
        form = RecurringExpenseForm(can_auto_debit=can_auto_debit)
    names = expense_selectors.used_expense_names(selectors.expenses_visible_to(request.user))
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Add fixed monthly expense — {villa.name}', 'cancel_url': back, 'expense_names': names, 'datalist_only': True})

@login_required
def recurring_edit(request, pk):
    recurring = get_object_or_404(RecurringExpense.objects.select_related('villa', 'villa__business', 'category'), pk=pk, villa__in=selectors.villas_visible_to(request.user))
    villa = _villa_for_fixed_expenses(request, recurring.villa_id)
    back = reverse('villas:villa_detail', args=[villa.pk]) + '#fixed-expenses'
    can_auto_debit = selectors.is_business_manager_of(request.user, villa.business)
    if request.method == 'POST':
        form = RecurringExpenseForm(request.POST, can_auto_debit=can_auto_debit)
        if form.is_valid():
            data = form.cleaned_data
            category = get_or_create_category(name=data['name'])
            clash = RecurringExpense.objects.filter(villa=villa, category=category).exclude(pk=recurring.pk).exists()
            if clash:
                form.add_error('name', f'{category.name} is already a fixed expense of this villa.')
            else:
                changes = {'category': category, 'amount': data['amount'], 'due_day': data['due_day']}
                if can_auto_debit:
                    changes['auto_debit'] = data.get('auto_debit', False)
                update_recurring_expense(recurring=recurring, updated_by=request.user, **changes)
                messages.success(request, f'“{category.name}” updated — it applies from this month; earlier months keep their amount.')
                return redirect(back)
    else:
        form = RecurringExpenseForm(can_auto_debit=can_auto_debit, initial={'name': recurring.category.name, 'amount': recurring.amount, 'due_day': recurring.due_day, 'auto_debit': recurring.auto_debit})
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
def expense_batch(request, action='paid'):
    """Bulk mark paid or bulk verify selected expenses in one tap."""
    ids = request.POST.getlist('expense_ids')
    qs = selectors.expenses_visible_to(request.user).filter(pk__in=ids, status=Expense.Status.ACTIVE).select_related('villa', 'business', 'category', 'recurring')
    count = 0
    for exp in qs:
        try:
            if action == 'paid' and not exp.paid_on and can_pay_expense(request.user, exp):
                admin = request.user.is_owner or selectors.is_business_manager_of(request.user, exp.business)
                mark_expense_paid(expense=exp, paid_on=date.today(), payment_method=Expense.Method.BANK_TRANSFER if admin else Expense.Method.CASH, marked_by=request.user)
                count += 1
            elif action == 'verify' and exp.paid_on and not exp.verified_at:
                verify_expense(expense=exp, verified_by=request.user)
                count += 1
        except (DomainError, PermissionDenied):
            pass
    if count:
        messages.success(request, f'{count} expense(s) {"verified" if action == "verify" else "marked as paid"}.')
    return redirect(_back_to(request, reverse('expenses:list')))

@login_required
@require_POST
def expense_quick_paid(request, pk):
    """One tap: 'Paid'. Staff-paid money awaits the admin's verification; the admin's own payment is final."""
    expense = get_object_or_404(selectors.expenses_visible_to(request.user).select_related('villa', 'business', 'category', 'recurring'), pk=pk)
    if not can_pay_expense(request.user, expense):
        raise PermissionDenied('You cannot settle this expense.')
    is_admin = request.user.is_owner or selectors.is_business_manager_of(request.user, expense.business)
    try:
        mark_expense_paid(expense=expense, paid_on=date.today(), payment_method=Expense.Method.BANK_TRANSFER if is_admin else Expense.Method.CASH, marked_by=request.user)
    except DomainError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f'{expense.category} marked paid.' if is_admin else f'{expense.category} marked paid — the admin will verify it.')
    return redirect(_back_to(request, reverse('expenses:list')))

@login_required
@require_POST
def expense_verify(request, pk):
    """One tap: the Admin / Business Manager confirms a staff-paid expense as received."""
    expense = get_object_or_404(selectors.expenses_visible_to(request.user).select_related('villa', 'business', 'category'), pk=pk)
    try:
        verify_expense(expense=expense, verified_by=request.user)
    except DomainError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f'{expense.category} verified.')
    return redirect(_back_to(request, reverse('expenses:list')))
