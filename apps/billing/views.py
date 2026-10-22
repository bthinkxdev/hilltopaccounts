from datetime import date
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.urls import reverse
from apps.accounts import selectors
from apps.accounts.models import User
from apps.shared.exceptions import DomainError
from apps.shared.forms import ReasonForm
from apps.shared.pagination import paginate_queryset
from apps.shared.periods import period_context
from apps.tenancy.models import Tenant
from . import selectors as billing_selectors
from .forms import ChargeForm, InvoiceGenerateForm, PaymentCorrectionForm, PaymentForm
from .models import Charge, ChargeType, Invoice
from .services import collect_rent, cancel_charge, cancel_invoice, cancel_payment, correct_payment, create_charge, generate_monthly_invoice, record_payment

def _can_manage(user, villa):
    if villa.is_sold:
        return False
    return user.is_owner or selectors.is_business_manager_of(user, villa.business) or selectors.is_villa_staff_of(user, villa)

@login_required
def charge_create(request, partition_pk):
    partition = get_object_or_404(selectors.partitions_visible_to(request.user).select_related('villa', 'villa__business'), pk=partition_pk)
    if partition.villa.is_sold:
        raise PermissionDenied('Cannot add charges to a sold villa.')
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
    period = period_context(request)
    year, month = period['year'], period['month']
    today = date.today()

    tab = request.GET.get('tab', 'all')
    if tab not in ('all', 'collected', 'pending'):
        tab = 'all'

    q = request.GET.get('q', '').strip()
    villa_id = request.GET.get('villa', '').strip()
    staff_id = request.GET.get('staff', '').strip()
    collector = request.GET.get('collector', '').strip()
    active_collector = collector or staff_id

    #if coming from dashboard with collector filter, default tab to 'collected' unless explicitly set
    if active_collector in ('staff', 'admin') and 'tab' not in request.GET:
        tab = 'collected'

    #base query of villas visible to the current user
    villas_qs = selectors.villas_visible_to(request.user).filter(is_archived=False, is_sold=False).select_related('business').prefetch_related('staff_assignments__user')

    #available staff list for the filter dropdown
    if request.user.is_owner:
        staff_users = list(User.objects.filter(is_active=True, is_owner=False).exclude(username='system').order_by('first_name', 'username'))
    else:
        staff_users = [request.user]

    active_collector_user = None
    if active_collector.isdigit():
        uid = int(active_collector)
        active_collector_user = next((s for s in staff_users if s.pk == uid), None)
        if not active_collector_user:
            active_collector_user = User.objects.filter(pk=uid).first()

    #filter by villa if selected
    if villa_id.isdigit():
        villas_qs = villas_qs.filter(pk=int(villa_id))

    all_filter_villas = list(selectors.villas_visible_to(request.user).filter(is_archived=False, is_sold=False).order_by('name'))
    villas = list(villas_qs.distinct().order_by('name'))

    v_ids = [v.pk for v in villas]
    partitions_qs = selectors.partitions_visible_to(request.user).filter(villa_id__in=v_ids).select_related('villa').prefetch_related('tenancies')
    tenants = list(selectors.tenants_visible_to(request.user).filter(partition__villa_id__in=v_ids, status=Tenant.Status.ACTIVE).select_related('partition'))
    tenant_by_partition = {t.partition_id: t for t in tenants}

    invoices = selectors.invoices_visible_to(request.user).filter(partition__villa_id__in=v_ids)

    villa_sections = []
    all_collection_rows = []
    total_all_count = 0
    total_collected_count = 0
    total_pending_count = 0

    portfolio_rent_expected = Decimal('0.00')
    portfolio_rent_collected = Decimal('0.00')
    portfolio_rent_pending = Decimal('0.00')

    portfolio_deposit_total = Decimal('0.00')
    portfolio_deposit_received = Decimal('0.00')
    portfolio_deposit_pending = Decimal('0.00')

    q_lower = q.lower()

    for villa in villas:
        v_partitions = [p for p in partitions_qs if p.villa_id == villa.pk]
        v_tenant_map = {p.pk: tenant_by_partition[p.pk] for p in v_partitions if p.pk in tenant_by_partition}

        rows, totals = billing_selectors.rent_sheet(
            partitions=v_partitions,
            tenant_by_partition=v_tenant_map,
            invoice_queryset=invoices.filter(partition__villa=villa),
            year=year,
            month=month,
            today=today
        )

        v_deposit_total = Decimal('0.00')
        v_deposit_received = Decimal('0.00')
        v_deposit_pending = Decimal('0.00')

        decorated_rows = []
        for r in rows:
            r['villa'] = villa
            t = r['tenant']
            dep_total = t.deposit or Decimal('0.00')
            dep_paid = t.deposit_paid or Decimal('0.00')
            dep_pending = t.deposit_pending or Decimal('0.00')

            r['contract_rent'] = r.get('rent_amount') or t.monthly_rent
            r['extra_charges'] = max(Decimal('0.00'), r.get('due', Decimal('0.00')) - r.get('rent_amount', Decimal('0.00')))
            r['deposit_total'] = dep_total
            r['deposit_paid'] = dep_paid
            r['deposit_pending'] = dep_pending
            r['deposit_status'] = t.deposit_status

            assigned_staff_ids = {a.user_id for a in villa.staff_assignments.all()}
            r['assigned_staff_ids'] = assigned_staff_ids
            r['assigned_staff'] = [a.user for a in villa.staff_assignments.all()]

            is_collected = (r['status'] == billing_selectors.RentStatus.COLLECTED)
            is_pending = not is_collected

            #search filtering
            if q_lower:
                match = (
                    q_lower in r['partition'].name.lower() or
                    q_lower in t.name.lower() or
                    q_lower in villa.name.lower() or
                    (t.mobile and q_lower in t.mobile.lower())
                )
                if not match:
                    continue

            #sort collector breakdown according to active filter
            if active_collector == 'staff':
                r['collector_breakdown'].sort(key=lambda x: (x['is_owner'], x['user'].username))
            elif active_collector == 'admin':
                r['collector_breakdown'].sort(key=lambda x: (not x['is_owner'], x['user'].username))
            elif active_collector.isdigit():
                uid = int(active_collector)
                r['collector_breakdown'].sort(key=lambda x: (x['user'].pk != uid, x['user'].username))

            #collector / staff matching
            match_collected = False
            match_pending = False
            if active_collector == 'staff':
                match_collected = (r.get('staff_collected', Decimal('0.00')) > Decimal('0.00'))
                match_pending = (is_pending and bool(assigned_staff_ids))
            elif active_collector == 'admin':
                match_collected = (r.get('admin_collected', Decimal('0.00')) > Decimal('0.00'))
                match_pending = (is_pending and not bool(assigned_staff_ids))
            elif active_collector.isdigit():
                uid = int(active_collector)
                match_collected = (uid in r.get('collector_user_ids', set()))
                match_pending = (is_pending and uid in assigned_staff_ids)
            else:
                match_collected = (r['paid'] > Decimal('0.00'))
                match_pending = is_pending

            match_all = match_collected or match_pending
            if not match_all:
                continue

            total_all_count += 1
            if match_collected:
                total_collected_count += 1
            if match_pending:
                total_pending_count += 1

            #tab filtering
            if tab == 'collected' and not match_collected:
                continue
            if tab == 'pending' and not match_pending:
                continue

            v_deposit_total += dep_total
            v_deposit_received += dep_paid
            v_deposit_pending += dep_pending

            #accumulate totals for matched rows
            if active_collector == 'staff':
                portfolio_rent_collected += r.get('staff_collected', Decimal('0.00'))
                portfolio_rent_expected += (r.get('staff_collected', Decimal('0.00')) if tab == 'collected' else r['due'])
                if tab != 'collected':
                    portfolio_rent_pending += (r['due'] if is_pending else Decimal('0.00'))
            elif active_collector == 'admin':
                portfolio_rent_collected += r.get('admin_collected', Decimal('0.00'))
                portfolio_rent_expected += (r.get('admin_collected', Decimal('0.00')) if tab == 'collected' else r['due'])
                if tab != 'collected':
                    portfolio_rent_pending += (r['due'] if is_pending else Decimal('0.00'))
            elif active_collector.isdigit():
                uid = int(active_collector)
                user_paid = sum((p.amount for p in r.get('payments', []) if p.collected_by_id == uid), start=Decimal('0.00'))
                portfolio_rent_collected += user_paid
                portfolio_rent_expected += (user_paid if tab == 'collected' else r['due'])
                if tab != 'collected':
                    portfolio_rent_pending += (r['due'] if is_pending else Decimal('0.00'))
            else:
                portfolio_rent_collected += r['paid']
                portfolio_rent_expected += r['due']
                portfolio_rent_pending += max(Decimal('0.00'), r['due'] - r['paid'])

            portfolio_deposit_total += dep_total
            portfolio_deposit_received += dep_paid
            portfolio_deposit_pending += dep_pending

            decorated_rows.append(r)
            all_collection_rows.append(r)

        assigned_staff = [a.user for a in villa.staff_assignments.all()]

        if decorated_rows:
            villa_sections.append({
                'villa': villa,
                'rows': decorated_rows,
                'assigned_staff': assigned_staff,
                'rent_expected': totals['expected'],
                'rent_collected': totals['collected'],
                'rent_pending': totals['pending'],
                'deposit_total': v_deposit_total,
                'deposit_received': v_deposit_received,
                'deposit_pending': v_deposit_pending,
            })

    has_filters = bool(q or active_collector or villa_id)

    keep = []
    if q: keep.append(('q', q))
    if tab != 'all': keep.append(('tab', tab))
    if villa_id: keep.append(('villa', villa_id))
    if staff_id: keep.append(('staff', staff_id))
    if collector: keep.append(('collector', collector))

    context = {
        'collection_rows': all_collection_rows,
        'villa_sections': villa_sections,
        'tab': tab,
        'tab_counts': {
            'all': total_all_count,
            'collected': total_collected_count,
            'pending': total_pending_count,
        },
        'portfolio_rent_expected': portfolio_rent_expected,
        'portfolio_rent_collected': portfolio_rent_collected,
        'portfolio_rent_pending': portfolio_rent_pending,
        'portfolio_deposit_total': portfolio_deposit_total,
        'portfolio_deposit_received': portfolio_deposit_received,
        'portfolio_deposit_pending': portfolio_deposit_pending,
        'staff_users': staff_users,
        'staff_id': staff_id,
        'collector': active_collector,
        'active_collector_user': active_collector_user,
        'villas': all_filter_villas,
        'villa_id': villa_id,
        'q': q,
        'keep': keep,
        'has_filters': has_filters,
        'can_manage': True,
    }
    context.update(period)
    return render(request, 'billing/payment_list.html', context)

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
    back = request.POST.get('next') or reverse('villas:villa_detail', args=[partition.villa_id])
    try:
        y_val = request.POST.get('year') or request.GET.get('year')
        m_val = request.POST.get('month') or request.GET.get('month')
        if not y_val or not m_val:
            collected_at_str = request.POST.get('collected_at')
            if collected_at_str:
                d = date.fromisoformat(collected_at_str)
                y_val, m_val = d.year, d.month
            else:
                today = date.today()
                y_val, m_val = today.year, today.month
        year, month = int(y_val), int(m_val)
        if not (2000 <= year <= 2100 and 1 <= month <= 12):
            raise ValueError
    except (ValueError, TypeError):
        messages.error(request, 'Choose a valid month first.')
        return redirect(back)
    method = request.POST.get('method', 'cash')
    if method not in ('cash', 'bank_transfer'):
        messages.error(request, 'Choose how the rent was paid.')
        return redirect(back)
    if not request.POST.get('next'):
        back = f'{back}?period=month&year={year}&month={month}#rent'
    if not _can_manage(request.user, partition.villa):
        raise PermissionDenied('You cannot collect rent for this villa.')
    amount_str = request.POST.get('amount', '').strip()
    rent_amount_str = request.POST.get('rent_amount', '').strip()
    try:
        amount = Decimal(amount_str) if amount_str else None
        rent_amount = Decimal(rent_amount_str) if rent_amount_str else None
    except Exception:
        messages.error(request, 'Please enter a valid amount.')
        return redirect(back)
    try:
        payment = collect_rent(
            partition=partition,
            year=year,
            month=month,
            method=method,
            collected_by=request.user,
            amount=amount,
            rent_amount=rent_amount,
        )
    except DomainError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f'{partition.name}: QAR {payment.amount} rent collected for {payment.invoice.billing_period_start:%B %Y}.')
    return redirect(back)

