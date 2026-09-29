from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from apps.accounts import selectors
from apps.shared.exceptions import DomainError
from apps.shared.forms import ReasonForm
from apps.shared.pagination import paginate_queryset
from . import selectors as billing_selectors
from .forms import ChargeForm, InvoiceGenerateForm, PaymentCorrectionForm, PaymentForm
from .models import Invoice
from .services import cancel_charge, cancel_invoice, cancel_payment, correct_payment, create_charge, generate_monthly_invoice, record_payment

def _can_manage(user, villa):
    return user.is_owner or selectors.is_business_manager_of(user, villa.business) or selectors.is_villa_staff_of(user, villa)

@login_required
def charge_create(request, partition_pk):
    partition = get_object_or_404(selectors.partitions_visible_to(request.user), pk=partition_pk)
    if not _can_manage(request.user, partition.villa):
        raise PermissionDenied('You cannot add a charge to this partition.')
    if request.method == 'POST':
        form = ChargeForm(request.POST)
        if form.is_valid():
            charge = create_charge(partition=partition, created_by=request.user, **form.cleaned_data)
            messages.success(request, f'Charge “{charge.charge_type}” added.')
            return redirect('villas:partition_detail', pk=partition.pk)
    else:
        form = ChargeForm()
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Add Charge — {partition.name}', 'cancel_url': reverse('villas:partition_detail', args=[partition.pk])})

@login_required
def charge_cancel(request, pk):
    from .models import Charge
    charge = get_object_or_404(selectors.charges_visible_to(request.user), pk=pk)
    if not _can_manage(request.user, charge.partition.villa):
        raise PermissionDenied('You cannot remove this charge.')
    if request.method == 'POST':
        form = ReasonForm(request.POST)
        if form.is_valid():
            cancel_charge(charge=charge, cancelled_by=request.user, reason=form.cleaned_data['reason'])
            messages.success(request, 'Charge deactivated.')
            return redirect('villas:partition_detail', pk=charge.partition.pk)
    else:
        form = ReasonForm()
    return render(request, 'components/confirm_reason.html', {'form': form, 'title': f'Deactivate {charge.charge_type}?', 'message': "Future invoices won't include this charge. Already-generated invoices are unaffected.", 'cancel_url': reverse('villas:partition_detail', args=[charge.partition.pk])})

@login_required
def invoice_list(request):
    qs = selectors.invoices_visible_to(request.user).select_related('partition', 'partition__villa', 'tenant')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(invoice_number__icontains=q) | Q(tenant__name__icontains=q))
    status = request.GET.get('status', '')
    if status:
        qs = billing_selectors.filter_by_status(qs, status)
    page_obj = paginate_queryset(request, qs.order_by('-issue_date'))
    for invoice in page_obj:
        invoice.computed_status = billing_selectors.invoice_status(invoice)
        invoice.computed_outstanding = billing_selectors.invoice_outstanding(invoice)
    return render(request, 'billing/invoice_list.html', {'page_obj': page_obj, 'q': q, 'status': status, 'status_choices': Invoice.Status.choices, 'breadcrumbs': [('Invoices', None)]})

@login_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(selectors.invoices_visible_to(request.user), pk=pk)
    payments = invoice.payments.select_related('collected_by').order_by('-collected_at')
    context = {'invoice': invoice, 'items': invoice.items.all(), 'payments': payments, 'computed_status': billing_selectors.invoice_status(invoice), 'computed_total': billing_selectors.invoice_total(invoice), 'computed_paid': billing_selectors.invoice_paid_amount(invoice), 'computed_outstanding': billing_selectors.invoice_outstanding(invoice), 'can_manage': _can_manage(request.user, invoice.villa), 'breadcrumbs': [('Invoices', reverse('billing:invoice_list')), (invoice.invoice_number, None)]}
    return render(request, 'billing/invoice_detail.html', context)

@login_required
def invoice_generate(request, partition_pk):
    partition = get_object_or_404(selectors.partitions_visible_to(request.user), pk=partition_pk)
    if not _can_manage(request.user, partition.villa):
        raise PermissionDenied('You cannot generate an invoice for this partition.')
    if request.method == 'POST':
        form = InvoiceGenerateForm(request.POST)
        if form.is_valid():
            try:
                invoice = generate_monthly_invoice(partition=partition, generated_by=request.user, **form.cleaned_data)
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f'Invoice {invoice.invoice_number} generated.')
                return redirect('billing:invoice_detail', pk=invoice.pk)
    else:
        form = InvoiceGenerateForm()
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Generate Invoice — {partition.name}', 'submit_label': 'Generate', 'cancel_url': reverse('villas:partition_detail', args=[partition.pk])})

@login_required
def invoice_cancel(request, pk):
    invoice = get_object_or_404(selectors.invoices_visible_to(request.user), pk=pk)
    if not _can_manage(request.user, invoice.villa):
        raise PermissionDenied('You cannot cancel this invoice.')
    if request.method == 'POST':
        form = ReasonForm(request.POST)
        if form.is_valid():
            cancel_invoice(invoice=invoice, cancelled_by=request.user, reason=form.cleaned_data['reason'])
            messages.success(request, f'Invoice {invoice.invoice_number} cancelled.')
            return redirect('billing:invoice_detail', pk=invoice.pk)
    else:
        form = ReasonForm()
    return render(request, 'components/confirm_reason.html', {'form': form, 'title': f'Cancel {invoice.invoice_number}?', 'message': 'The invoice stays on record but is excluded from every financial total.', 'cancel_url': reverse('billing:invoice_detail', args=[invoice.pk])})

@login_required
def payment_list(request):
    qs = selectors.payments_visible_to(request.user).select_related('invoice', 'collected_by')
    method = request.GET.get('method', '')
    if method:
        qs = qs.filter(method=method)
    page_obj = paginate_queryset(request, qs.order_by('-collected_at'))
    return render(request, 'billing/payment_list.html', {'page_obj': page_obj, 'method': method, 'breadcrumbs': [('Collections', None)]})

@login_required
def payment_record(request, invoice_pk):
    invoice = get_object_or_404(selectors.invoices_visible_to(request.user), pk=invoice_pk)
    if not (request.user.is_owner or selectors.can_manage_villas_and_tenants(request.user)):
        raise PermissionDenied('You are not authorized to record collections.')
    if request.method == 'POST':
        form = PaymentForm(request.POST)
        if form.is_valid():
            try:
                payment = record_payment(invoice=invoice, collected_by=request.user, created_by=request.user, **form.cleaned_data)
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f'Payment of QAR {payment.amount} recorded.')
                return redirect('billing:invoice_detail', pk=invoice.pk)
    else:
        form = PaymentForm(initial={'amount': billing_selectors.invoice_outstanding(invoice)})
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Record Collection — {invoice.invoice_number}', 'submit_label': 'Record Payment', 'cancel_url': reverse('billing:invoice_detail', args=[invoice.pk])})

@login_required
def payment_cancel(request, pk):
    payment = get_object_or_404(selectors.payments_visible_to(request.user), pk=pk)
    if not _can_manage(request.user, payment.invoice.villa):
        raise PermissionDenied('You cannot cancel this payment.')
    if request.method == 'POST':
        form = ReasonForm(request.POST)
        if form.is_valid():
            cancel_payment(payment=payment, cancelled_by=request.user, reason=form.cleaned_data['reason'])
            messages.success(request, 'Payment cancelled.')
            return redirect('billing:invoice_detail', pk=payment.invoice.pk)
    else:
        form = ReasonForm()
    return render(request, 'components/confirm_reason.html', {'form': form, 'title': f'Cancel payment of QAR {payment.amount}?', 'message': 'The original record is preserved and marked cancelled — never edited or deleted.', 'cancel_url': reverse('billing:invoice_detail', args=[payment.invoice.pk])})

@login_required
def payment_correct(request, pk):
    payment = get_object_or_404(selectors.payments_visible_to(request.user), pk=pk)
    if not _can_manage(request.user, payment.invoice.villa):
        raise PermissionDenied('You cannot correct this payment.')
    if request.method == 'POST':
        form = PaymentCorrectionForm(request.POST)
        if form.is_valid():
            try:
                correct_payment(payment=payment, new_amount=form.cleaned_data['new_amount'], corrected_by=request.user, reason=form.cleaned_data['reason'])
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, 'Payment corrected — a new entry replaces the original.')
                return redirect('billing:invoice_detail', pk=payment.invoice.pk)
    else:
        form = PaymentCorrectionForm(initial={'new_amount': payment.amount})
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Correct payment of QAR {payment.amount}', 'submit_label': 'Save Correction', 'cancel_url': reverse('billing:invoice_detail', args=[payment.invoice.pk])})
