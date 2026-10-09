from datetime import date
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.urls import reverse
from apps.accounts import selectors
from apps.shared.exceptions import DomainError
from apps.shared.forms import ReasonForm
from apps.shared.pagination import paginate_queryset
from . import selectors as billing_selectors
from .forms import ChargeForm, InvoiceGenerateForm, PaymentCorrectionForm, PaymentForm
from .models import Charge, ChargeType, Invoice
from .services import collect_rent, cancel_charge, cancel_invoice, cancel_payment, correct_payment, create_charge, generate_monthly_invoice, record_payment

def _can_manage(user, villa):
    return user.is_owner or selectors.is_business_manager_of(user, villa.business) or selectors.is_villa_staff_of(user, villa)

@login_required
def charge_create(request, partition_pk):
    partition = get_object_or_404(selectors.partitions_visible_to(request.user).select_related('villa', 'villa__business'), pk=partition_pk)
    if not _can_manage(request.user, partition.villa):
        raise PermissionDenied('You cannot add a charge to this partition.')
    today = date.today()
    active_type_ids = Charge.objects.filter(partition=partition, is_active=True).filter(Q(end_date__isnull=True) | Q(end_date__gt=today)).values_list('charge_type_id', flat=True)
    if not ChargeType.objects.filter(is_active=True).exclude(id__in=active_type_ids).exists():
        messages.info(request, 'All available charges have already been added.')
        return redirect('villas:partition_detail', pk=partition.pk)
    rent_type = ChargeType.objects.filter(name__iexact='Rent', is_active=True).first()
    current_tenant = partition.current_tenant
    assigned_rent = (current_tenant.monthly_rent if current_tenant else None) or partition.rent
    if request.method == 'POST':
        form = ChargeForm(request.POST, partition=partition)
        if form.is_valid():
            try:
                charge = create_charge(partition=partition, created_by=request.user, **form.cleaned_data)
                messages.success(request, f'Charge “{charge.charge_type}” added.')
                return redirect('villas:partition_detail', pk=partition.pk)
            except DomainError as exc:
                form.add_error('charge_type', str(exc))
    else:
        initial = {}
        if current_tenant:
            initial['start_date'] = current_tenant.move_in_date
        else:
            initial['start_date'] = date.today()
        form = ChargeForm(initial=initial, partition=partition)

    prefix = [('Villas', reverse('villas:villa_list'))] if selectors.is_field_staff(request.user) else [('Businesses', reverse('businesses:list')), (partition.villa.business.name, reverse('businesses:detail', args=[partition.villa.business.pk]))]
    breadcrumbs = prefix + [
        (partition.villa.name, reverse('villas:villa_detail', args=[partition.villa.pk])),
        (partition.name, reverse('villas:partition_detail', args=[partition.pk])),
        ('Add Charge', None),
    ]
    return render(request, 'components/form_page.html', {
        'form': form,
        'title': f'Add Charge — {partition.name}',
        'cancel_url': reverse('villas:partition_detail', args=[partition.pk]),
        'breadcrumbs': breadcrumbs,
        'charge_form': True,
        'rent_amount': str(assigned_rent) if assigned_rent else '',
        'rent_charge_type_id': str(rent_type.pk) if rent_type else '',
    })

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
    page_obj = paginate_queryset(request, qs.order_by('-created_at', '-id'))
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
            try:
                cancel_invoice(invoice=invoice, cancelled_by=request.user, reason=form.cleaned_data['reason'])
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
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
            try:
                cancel_payment(payment=payment, cancelled_by=request.user, reason=form.cleaned_data['reason'])
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
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


@login_required
@require_POST
def rent_collect(request, partition_pk):
    """Staff taps 'Collected' on the month's rent sheet. POST only."""
    partition = get_object_or_404(selectors.partitions_visible_to(request.user).select_related('villa'), pk=partition_pk)
    back = reverse('villas:villa_detail', args=[partition.villa_id])
    try:
        year, month = int(request.POST.get('year', '')), int(request.POST.get('month', ''))
        if not (2000 <= year <= 2100 and 1 <= month <= 12):
            raise ValueError
    except ValueError:
        messages.error(request, 'Choose a valid month first.')
        return redirect(back)
    method = request.POST.get('method', 'cash')
    if method not in ('cash', 'bank_transfer'):
        messages.error(request, 'Choose how the rent was paid.')
        return redirect(back)
    back = f'{back}?period=month&year={year}&month={month}#rent'
    if not _can_manage(request.user, partition.villa):
        raise PermissionDenied('You cannot collect rent for this villa.')
    try:
        payment = collect_rent(partition=partition, year=year, month=month, method=method, collected_by=request.user)
    except DomainError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f'{partition.name}: QAR {payment.amount} rent collected for {payment.invoice.billing_period_start:%B %Y}.')
    return redirect(back)
