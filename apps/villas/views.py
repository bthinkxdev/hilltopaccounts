from datetime import date
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from apps.accounts import selectors
from apps.accounts.models import Assignment, User
from apps.businesses.forms import ArchiveReasonForm
from apps.businesses.models import Business
from apps.shared.forms import ReasonForm
from apps.shared.pagination import paginate_queryset
from apps.shared.periods import period_context
from django.db import transaction
from apps.expenses.services import create_recurring_expense, get_or_create_category
from .forms import FIXED_EXPENSE_DEFAULTS, PartitionForm, PhotoForm, VillaForm
from apps.expenses.models import RecurringExpense
from .models import Photo
from .services import add_photo, archive_partition, archive_villa, create_partition, create_villa, delete_photo, update_partition

def _first_photo_by(photos, key):
    """Map group id -> first photo, from a single already-fetched list of photos."""
    first = {}
    for photo in photos:
        first.setdefault(key(photo), photo)
    return first

@login_required
def villa_list(request):
    from apps.billing import selectors as billing_selectors
    today = date.today()
    base = selectors.villas_visible_to(request.user).filter(is_archived=False)
    q = request.GET.get('q', '').strip()
    if q:
        base = base.filter(name__icontains=q)
    business_id = request.GET.get('business')
    if business_id and business_id.isdigit():
        base = base.filter(business_id=business_id)
    staff_id = request.GET.get('staff', '')
    staff_choices = []
    financial_scope = selectors.financial_villas(request.user)
    if financial_scope.exists():
        staff_choices = list(User.objects.filter(assignments__role=Assignment.Role.VILLA_STAFF, assignments__villa__in=financial_scope).distinct().order_by('first_name', 'username'))
        if staff_id.isdigit():
            base = base.filter(pk__in=Assignment.objects.filter(role=Assignment.Role.VILLA_STAFF, user_id=staff_id, villa__in=financial_scope).values('villa_id'))
    qs = base.select_related('business').annotate(partition_total=Count('partitions', distinct=True), occupied_total=Count('partitions', filter=Q(partitions__tenancies__status='active'), distinct=True)).order_by('business__name', 'name')
    page_obj = paginate_queryset(request, qs, per_page=12)
    villa_ids = [villa.pk for villa in page_obj]
    period = period_context(request, today)
    if period['period'] == 'month':
        from apps.expenses.services import ensure_fixed_expenses
        ensure_fixed_expenses(villa_ids=villa_ids, year=period['year'], month=period['month'])
    covers = _first_photo_by(Photo.objects.filter(villa_id__in=villa_ids, partition__isnull=True), lambda photo: photo.villa_id)
    financial_ids = set(selectors.financial_villas(request.user).filter(pk__in=villa_ids).values_list('pk', flat=True))
    pnl_by_villa = {}
    if financial_ids:
        pnl_by_villa = billing_selectors.profit_loss_by_villa(selectors.invoices_visible_to(request.user).filter(partition__villa__in=financial_ids), selectors.expenses_visible_to(request.user).filter(villa__in=financial_ids), period['period_start'], period['period_end'])
    from apps.cash.models import CashHandover
    from apps.expenses import selectors as expenses_selectors
    from apps.expenses.models import Expense
    from apps.billing.models import Payment
    from apps.villas.models import Partition
    staff_names = {}
    for assignment in Assignment.objects.filter(role=Assignment.Role.VILLA_STAFF, villa_id__in=financial_ids).select_related('user'):
        staff_names.setdefault(assignment.villa_id, []).append(assignment.user.get_full_name() or assignment.user.username)
    pending_rent = billing_selectors.pending_rent_by_villa(selectors.invoices_visible_to(request.user).filter(partition__villa_id__in=villa_ids), selectors.partitions_visible_to(request.user).filter(villa_id__in=villa_ids), period['year'], period['month']) if period['period'] == 'month' else {}
    fixed_pending = expenses_selectors.fixed_pending_by_villa(villa_ids, period['year'], period['month']) if period['period'] == 'month' else {}
    overdue_expenses = {row['villa_id']: row['n'] for row in selectors.expenses_visible_to(request.user).filter(villa_id__in=villa_ids, status=Expense.Status.ACTIVE, paid_on__isnull=True, due_date__lt=today).order_by().values('villa_id').annotate(n=Count('pk'))}
    to_verify = {row['villa_id']: row['n'] for row in selectors.expenses_visible_to(request.user).filter(villa_id__in=financial_ids, status=Expense.Status.ACTIVE, paid_on__isnull=False, verified_at__isnull=True).order_by().values('villa_id').annotate(n=Count('pk'))}
    unconfirmed_cash = {row['invoice__partition__villa_id']: row['total'] for row in Payment.objects.filter(handovers__status=CashHandover.Status.SUBMITTED, invoice__partition__villa_id__in=financial_ids).order_by().values('invoice__partition__villa_id').annotate(total=Sum('amount'))}
    for villa in page_obj:
        villa.staff_names = staff_names.get(villa.pk, [])
        villa.pending_rent = pending_rent.get(villa.pk, 0)
        villa.overdue_expense_count = overdue_expenses.get(villa.pk, 0)
        villa.fixed_pending = fixed_pending.get(villa.pk, 0)
        villa.to_verify = to_verify.get(villa.pk, 0)
        villa.unconfirmed_cash = unconfirmed_cash.get(villa.pk)
        villa.cover = covers.get(villa.pk)
        villa.vacant_total = villa.partition_total - villa.occupied_total
        villa.pnl = pnl_by_villa.get(villa.pk, billing_selectors.EMPTY_PROFIT_LOSS) if villa.pk in financial_ids else None
    totals = None
    scoped_financial = selectors.financial_villas(request.user).filter(pk__in=base.values('pk'))
    if scoped_financial.exists():
        totals = billing_selectors.period_profit_loss(selectors.invoices_visible_to(request.user).filter(partition__villa__in=scoped_financial), selectors.expenses_visible_to(request.user).filter(villa__in=scoped_financial), period['period_start'], period['period_end'])
    keep = [(name, value) for name, value in (('q', q), ('business', business_id or ''), ('staff', staff_id)) if value]
    context = {'staff_choices': staff_choices, 'selected_staff': staff_id, 'page_obj': page_obj, 'q': q, 'totals': totals, 'keep': keep, 'businesses': selectors.businesses_visible_to(request.user).filter(is_archived=False), 'selected_business': business_id, 'can_create_villa': selectors.businesses_managed_by(request.user).exists(), 'breadcrumbs': [('Villas', None)], **period}
    return render(request, 'villas/villa_list.html', context)

@login_required
def villa_detail(request, pk):
    from apps.audit.models import AuditLog
    from apps.billing import selectors as billing_selectors
    from apps.cash import selectors as cash_selectors
    from apps.expenses import selectors as expenses_selectors
    from apps.expenses.services import ensure_fixed_expenses
    from apps.tenancy.models import Tenant
    today = date.today()
    villa = get_object_or_404(selectors.villas_visible_to(request.user).select_related('business'), pk=pk)
    period = period_context(request, today)
    start, end = period['period_start'], period['period_end']
    # Fixed expenses show up by themselves every month (idempotent, system-attributed) — nobody re-adds them.
    for month in ([period['month']] if period['period'] == 'month' else range(1, 13)):
        ensure_fixed_expenses(villa_ids=[villa.pk], year=period['year'], month=month)
    scope = selectors.ManageScope(request.user)
    can_manage = scope.can_manage_villa(villa.pk, villa.business_id)
    is_admin = scope.is_admin(villa.business_id)
    partitions = list(selectors.partitions_visible_to(request.user).filter(villa=villa).order_by('name'))
    tenants = list(selectors.tenants_visible_to(request.user).filter(partition__villa=villa, status=Tenant.Status.ACTIVE).select_related('partition'))
    tenant_by_partition = {t.partition_id: t for t in tenants}
    photos = list(Photo.objects.filter(villa=villa).select_related('partition'))
    partition_covers = _first_photo_by([p for p in photos if p.partition_id], lambda photo: photo.partition_id)
    invoices = selectors.invoices_visible_to(request.user).filter(partition__villa=villa)
    expenses = selectors.expenses_visible_to(request.user).filter(villa=villa)
    can_view_financials = selectors.can_view_villa_financials(request.user, villa)
    partition_pnl = billing_selectors.profit_loss_by_partition(invoices, expenses, start, end) if can_view_financials else {}
    for partition in partitions:
        partition.tenant = tenant_by_partition.get(partition.pk)
        partition.cover = partition_covers.get(partition.pk)
        partition.pnl = partition_pnl.get(partition.pk, billing_selectors.EMPTY_PROFIT_LOSS) if can_view_financials else None

    def decorate(expense):
        expense.due_state = expenses_selectors.due_state(expense, today)
        expense.can_pay = scope.can_pay_expense(expense)
        expense.can_verify = scope.can_verify_expense(expense)
        expense.can_edit = can_manage and expense.status == 'active' and expense.paid_on is None
        return expense

    one_off = [decorate(e) for e in expenses.filter(date__gte=start, date__lte=end, recurring__isnull=True).select_related('category', 'partition', 'created_by').order_by('-date', '-id')[:15]]
    rent_rows, rent_totals = (billing_selectors.rent_sheet(partitions=partitions, tenant_by_partition=tenant_by_partition, invoice_queryset=invoices, year=period['year'], month=period['month'], today=today) if period['period'] == 'month' else (None, None))
    fixed_rows, fixed_totals = expenses_selectors.fixed_expense_checklist(villa, period['year'], period['month'], today) if period['period'] == 'month' else (None, None)
    for row in fixed_rows or []:
        if row['expense'] is not None:
            decorate(row['expense'])

    def compute_fixed_subset(rows):
        zero = Decimal('0.00')
        sub = {'total': zero, 'paid': zero, 'unpaid': zero, 'unpaid_count': 0}
        for r in rows:
            if r['status'] != expenses_selectors.FixedStatus.CANCELLED:
                sub['total'] += r['amount']
                if r['status'] in (expenses_selectors.FixedStatus.PAID, expenses_selectors.FixedStatus.TO_VERIFY):
                    sub['paid'] += r['amount']
                else:
                    sub['unpaid'] += r['amount']
                    sub['unpaid_count'] += 1
        return sub

    owner_fixed_rows = [r for r in (fixed_rows or []) if getattr(r['template'], 'paid_by', '') == 'owner'] if fixed_rows is not None else None
    staff_fixed_rows = [r for r in (fixed_rows or []) if getattr(r['template'], 'paid_by', '') == 'staff'] if fixed_rows is not None else None
    owner_fixed_totals = compute_fixed_subset(owner_fixed_rows) if owner_fixed_rows is not None else None
    staff_fixed_totals = compute_fixed_subset(staff_fixed_rows) if staff_fixed_rows is not None else None

    fixed_templates = list(RecurringExpense.objects.filter(villa=villa, is_active=True).select_related('category')) if period['period'] == 'year' else []
    owner_fixed_templates = [t for t in fixed_templates if t.paid_by == 'owner']
    staff_fixed_templates = [t for t in fixed_templates if t.paid_by == 'staff']

    owner_one_off = [e for e in one_off if e.paid_by == 'owner']
    staff_one_off = [e for e in one_off if e.paid_by == 'staff']

    villa_photos = [p for p in photos if not p.partition_id]
    context = {
        'villa': villa,
        'partitions': partitions,
        'occupied_count': len(tenants),
        'vacant_count': len(partitions) - len(tenants),
        'can_manage': can_manage,
        'can_admin_fixed': is_admin,
        'is_admin': is_admin,
        'can_delete_photos': is_admin,
        'can_view_financials': can_view_financials,
        'photos': villa_photos,
        'rent_rows': rent_rows,
        'rent_totals': rent_totals,
        'fixed_rows': fixed_rows,
        'fixed_totals': fixed_totals,
        'owner_fixed_rows': owner_fixed_rows,
        'owner_fixed_totals': owner_fixed_totals,
        'staff_fixed_rows': staff_fixed_rows,
        'staff_fixed_totals': staff_fixed_totals,
        'fixed_templates': fixed_templates,
        'owner_fixed_templates': owner_fixed_templates,
        'staff_fixed_templates': staff_fixed_templates,
        'one_off_expenses': one_off,
        'owner_one_off': owner_one_off,
        'staff_one_off': staff_one_off,
        'cash': cash_selectors.villa_cash_position(villa),
        'to_verify': expenses_selectors.due_summary(expenses, today)['verify'] if is_admin else None,
        'keep': [],
        'breadcrumbs': [('Villas', reverse('villas:villa_list')), (villa.name, None)],
        **period,
    }
    if can_view_financials:
        context['pnl'] = billing_selectors.period_profit_loss(invoices, expenses, start, end)
        context['total_outstanding'] = billing_selectors.outstanding_amount(invoices)
        context['activity'] = AuditLog.objects.filter(villa=villa).select_related('user').order_by('-timestamp')[:8]
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
            data = dict(form.cleaned_data)
            fixed_amounts = [(label, data.pop(field), paid_by) for field, label, paid_by in FIXED_EXPENSE_DEFAULTS]
            business = data.pop('business')
            with transaction.atomic():
                villa = create_villa(business=business, created_by=request.user, **data)
                if selectors.is_business_manager_of(request.user, business):
                    for label, amount, paid_by in fixed_amounts:
                        if amount:
                            create_recurring_expense(villa=villa, category=get_or_create_category(name=label), amount=amount, due_day=1, paid_by=paid_by, created_by=request.user)
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
    from apps.billing import selectors as billing_selectors
    from apps.billing.models import Charge
    from apps.expenses import selectors as expenses_selectors
    today = date.today()
    partition = get_object_or_404(selectors.partitions_visible_to(request.user).select_related('villa', 'villa__business'), pk=pk)
    villa = partition.villa
    period = period_context(request, today)
    start, end = period['period_start'], period['period_end']
    current_tenant = partition.current_tenant
    all_invoices = selectors.invoices_visible_to(request.user).filter(partition=partition)
    invoices = list(all_invoices.order_by('-created_at', '-id')[:20])
    for invoice in invoices:
        invoice.computed_status = billing_selectors.invoice_status(invoice)
        invoice.computed_outstanding = billing_selectors.invoice_outstanding(invoice)
    expenses = selectors.expenses_visible_to(request.user).filter(partition=partition)
    recent_expenses = list(expenses.filter(date__gte=start, date__lte=end).select_related('category').order_by('-date', '-id')[:8])
    scope = selectors.ManageScope(request.user)
    can_manage_here = scope.can_manage_villa(villa.pk, villa.business_id)
    for expense in recent_expenses:
        expense.due_state = expenses_selectors.due_state(expense, today)
        expense.can_pay = scope.can_pay_expense(expense)
        expense.can_verify = scope.can_verify_expense(expense)
        expense.can_edit = can_manage_here and expense.status == 'active' and expense.paid_on is None
    can_view_financials = selectors.can_view_villa_financials(request.user, villa)
    context = {'partition': partition, 'villa': villa, 'current_tenant': current_tenant, 'charges': Charge.objects.filter(partition=partition, is_active=True) if current_tenant else Charge.objects.none(), 'invoices': invoices, 'recent_expenses': recent_expenses, 'photos': list(Photo.objects.filter(partition=partition)), 'can_manage': selectors.can_manage_villa(request.user, villa), 'can_delete_photos': request.user.is_owner or selectors.is_business_manager_of(request.user, villa.business), 'can_view_financials': can_view_financials, 'keep': [], 'breadcrumbs': [('Villas', reverse('villas:villa_list')), (villa.name, reverse('villas:villa_detail', args=[villa.pk])), (partition.name, None)], **period}
    if can_view_financials:
        context['pnl'] = billing_selectors.period_profit_loss(all_invoices, expenses, start, end)
        context['outstanding'] = billing_selectors.outstanding_amount(all_invoices)
    return render(request, 'villas/partition_detail.html', context)

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
def partition_edit(request, pk):
    partition = get_object_or_404(selectors.partitions_visible_to(request.user).select_related('villa', 'villa__business'), pk=pk)
    villa = partition.villa
    if not (request.user.is_owner or selectors.can_manage_villa(request.user, villa)):
        raise PermissionDenied('You cannot edit this partition.')
    if request.method == 'POST':
        form = PartitionForm(request.POST)
        form.fields.pop('villa')
        if form.is_valid():
            update_partition(
                partition=partition,
                updated_by=request.user,
                name=form.cleaned_data['name'],
                rent=form.cleaned_data.get('rent'),
                description=form.cleaned_data.get('description', ''),
            )
            messages.success(request, f'Partition “{partition.name}” updated.')
            return redirect('villas:partition_detail', pk=partition.pk)
    else:
        form = PartitionForm(initial={
            'name': partition.name,
            'rent': partition.rent,
            'description': partition.description,
        })
        form.fields.pop('villa')
    cancel_url = reverse('villas:partition_detail', args=[partition.pk])
    breadcrumbs = [
        ('Businesses', reverse('businesses:list')),
        (villa.business.name, reverse('businesses:detail', args=[villa.business.pk])),
        (villa.name, reverse('villas:villa_detail', args=[villa.pk])),
        (partition.name, reverse('villas:partition_detail', args=[partition.pk])),
        ('Edit Partition', None),
    ]
    return render(request, 'components/form_page.html', {
        'form': form,
        'title': f'Edit {partition.name}',
        'submit_label': 'Save Changes',
        'cancel_url': cancel_url,
        'breadcrumbs': breadcrumbs,
    })

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


def _photo_redirect(villa, partition):
    return redirect('villas:partition_detail', pk=partition.pk) if partition else redirect('villas:villa_detail', pk=villa.pk)

@login_required
def photo_add(request, villa_pk=None, partition_pk=None):
    partition = get_object_or_404(selectors.partitions_visible_to(request.user).select_related('villa'), pk=partition_pk) if partition_pk else None
    villa = partition.villa if partition else get_object_or_404(selectors.villas_visible_to(request.user), pk=villa_pk)
    if not selectors.can_manage_villa(request.user, villa):
        raise PermissionDenied('You cannot add photos here.')
    if request.method == 'POST':
        form = PhotoForm(request.POST, request.FILES)
        if form.is_valid():
            add_photo(villa=villa, partition=partition, image=form.cleaned_data['image'], caption=form.cleaned_data['caption'], uploaded_by=request.user)
            messages.success(request, 'Photo added.')
            return _photo_redirect(villa, partition)
    else:
        form = PhotoForm()
    target = partition.name if partition else villa.name
    cancel_url = reverse('villas:partition_detail', args=[partition.pk]) if partition else reverse('villas:villa_detail', args=[villa.pk])
    return render(request, 'components/form_page.html', {'form': form, 'title': f'Add Photo — {target}', 'submit_label': 'Upload', 'cancel_url': cancel_url})

@login_required
def photo_file(request, pk):
    """Photos are served through this view so they obey the same villa scoping as every other record."""
    from django.http import FileResponse, Http404
    from .photos import ALLOWED_EXTENSIONS
    photo = get_object_or_404(Photo.objects.filter(villa__in=selectors.villas_visible_to(request.user)), pk=pk)
    extension = photo.image.name.rsplit('.', 1)[-1].lower()
    try:
        handle = photo.image.open('rb')
    except FileNotFoundError as exc:
        raise Http404('Photo file is missing.') from exc
    response = FileResponse(handle, content_type=ALLOWED_EXTENSIONS.get(extension, 'application/octet-stream'))
    response['Cache-Control'] = 'private, max-age=3600'
    return response

@login_required
def photo_delete(request, pk):
    photo = get_object_or_404(Photo.objects.filter(villa__in=selectors.villas_visible_to(request.user)).select_related('villa', 'partition'), pk=pk)
    if not (request.user.is_owner or selectors.is_business_manager_of(request.user, photo.villa.business)):
        raise PermissionDenied('You cannot delete this photo.')
    villa, partition = photo.villa, photo.partition
    if request.method == 'POST':
        form = ReasonForm(request.POST)
        if form.is_valid():
            delete_photo(photo=photo, deleted_by=request.user, reason=form.cleaned_data['reason'])
            messages.success(request, 'Photo removed.')
            return _photo_redirect(villa, partition)
    else:
        form = ReasonForm()
    back = reverse('villas:partition_detail', args=[partition.pk]) if partition else reverse('villas:villa_detail', args=[villa.pk])
    return render(request, 'components/confirm_reason.html', {'form': form, 'title': 'Remove this photo?', 'message': 'The photo is deleted; the removal is recorded in the audit log.', 'cancel_url': back})
