from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from apps.accounts import selectors as accounts_selectors
from apps.shared.exceptions import DomainError
from apps.shared.forms import ReasonForm
from apps.shared.pagination import paginate_queryset
from . import selectors
from .forms import ConfirmHandoverForm, HandoverSubmitForm
from .models import CashHandover
from .services import confirm_cash_handover, reject_cash_handover, submit_cash_handover

@login_required
def handover_list(request):
    qs = accounts_selectors.cash_handovers_visible_to(request.user).select_related('staff')
    status = request.GET.get('status', '')
    if status:
        qs = qs.filter(status=status)
    page_obj = paginate_queryset(request, qs.order_by('-submitted_at'))
    return render(request, 'cash/handover_list.html', {'page_obj': page_obj, 'status': status, 'status_choices': CashHandover.Status.choices, 'my_outstanding_cash': selectors.outstanding_cash_for(request.user), 'my_pending_amount': selectors.pending_handover_amount(request.user), 'my_unclaimed_payments': selectors.unclaimed_cash_payments(request.user), 'breadcrumbs': [('Cash Handover', None)]})

@login_required
def handover_detail(request, pk):
    handover = get_object_or_404(accounts_selectors.cash_handovers_visible_to(request.user), pk=pk)
    can_confirm = handover.status == CashHandover.Status.SUBMITTED and handover.staff_id != request.user.pk and accounts_selectors.can_view_financial_kpis(request.user)
    return render(request, 'cash/handover_detail.html', {'handover': handover, 'payments': handover.payments.select_related('invoice'), 'can_confirm': can_confirm, 'breadcrumbs': [('Cash Handover', reverse('cash:handover_list')), (f'#{handover.pk}', None)]})

@login_required
def handover_submit(request):
    if request.method == 'POST':
        form = HandoverSubmitForm(request.POST, staff=request.user)
        if form.is_valid():
            try:
                handover = submit_cash_handover(staff=request.user, payments=form.cleaned_data['payments'], submitted_by=request.user, notes=form.cleaned_data['notes'])
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f'Handover of QAR {handover.declared_amount} submitted.')
                return redirect('cash:handover_detail', pk=handover.pk)
    else:
        form = HandoverSubmitForm(staff=request.user)
    if not form.fields['payments'].queryset.exists():
        messages.info(request, 'You have no un-handed-over cash collections right now.')
        return redirect('cash:handover_list')
    return render(request, 'components/form_page.html', {'form': form, 'title': 'Submit Cash Handover', 'submit_label': 'Submit', 'cancel_url': reverse('cash:handover_list')})

@login_required
def handover_confirm(request, pk):
    handover = get_object_or_404(accounts_selectors.cash_handovers_visible_to(request.user), pk=pk)
    if not accounts_selectors.can_view_financial_kpis(request.user):
        raise PermissionDenied('You cannot confirm cash handovers.')
    if request.method == 'POST':
        form = ConfirmHandoverForm(request.POST)
        if form.is_valid():
            try:
                confirm_cash_handover(handover=handover, confirmed_by=request.user, **form.cleaned_data)
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, 'Handover confirmed.')
                return redirect('cash:handover_detail', pk=handover.pk)
    else:
        form = ConfirmHandoverForm(initial={'confirmed_amount': handover.declared_amount})
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Confirm Handover — QAR {handover.declared_amount} declared', 'submit_label': 'Confirm', 'cancel_url': reverse('cash:handover_detail', args=[handover.pk])})

@login_required
def handover_reject(request, pk):
    handover = get_object_or_404(accounts_selectors.cash_handovers_visible_to(request.user), pk=pk)
    if not accounts_selectors.can_view_financial_kpis(request.user):
        raise PermissionDenied('You cannot reject cash handovers.')
    if request.method == 'POST':
        form = ReasonForm(request.POST)
        if form.is_valid():
            try:
                reject_cash_handover(handover=handover, rejected_by=request.user, reason=form.cleaned_data['reason'])
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, 'Handover rejected — the collections are available for resubmission.')
                return redirect('cash:handover_detail', pk=handover.pk)
    else:
        form = ReasonForm()
    return render(request, 'components/confirm_reason.html', {'form': form, 'title': 'Reject this handover?', 'message': "The staff member's collections become available for a new handover.", 'cancel_url': reverse('cash:handover_detail', args=[handover.pk])})
