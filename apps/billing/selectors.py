from datetime import date, timedelta
from decimal import Decimal
from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from .models import Charge, Invoice, InvoiceItem, Payment
_MONEY_FIELD = DecimalField(max_digits=12, decimal_places=2)

def invoice_total(invoice: Invoice) -> Decimal:
    return invoice.items.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

def invoice_paid_amount(invoice: Invoice) -> Decimal:
    if invoice.is_cancelled:
        return Decimal('0.00')
    return invoice.payments.filter(is_cancelled=False).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

def invoice_outstanding(invoice: Invoice) -> Decimal:
    if invoice.is_cancelled:
        return Decimal('0.00')
    return invoice_total(invoice) - invoice_paid_amount(invoice)

def invoice_status(invoice: Invoice) -> str:
    if invoice.is_cancelled:
        return Invoice.Status.CANCELLED
    total = invoice_total(invoice)
    paid = invoice_paid_amount(invoice)
    if paid >= total and total > 0:
        return Invoice.Status.PAID
    if paid > 0:
        base_status = Invoice.Status.PARTIALLY_PAID
    else:
        base_status = Invoice.Status.PENDING
    if invoice.due_date < date.today() and paid < total:
        return Invoice.Status.OVERDUE
    return base_status

def expected_revenue(invoice_queryset) -> Decimal:
    return InvoiceItem.objects.filter(invoice__in=invoice_queryset.filter(is_cancelled=False)).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

def collected_amount(invoice_queryset) -> Decimal:
    return Payment.objects.filter(invoice__in=invoice_queryset, is_cancelled=False).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

def outstanding_amount(invoice_queryset) -> Decimal:
    return expected_revenue(invoice_queryset) - collected_amount(invoice_queryset)

def collection_rate(invoice_queryset) -> Decimal:
    revenue = expected_revenue(invoice_queryset)
    if revenue == 0:
        return Decimal('0.00')
    return (collected_amount(invoice_queryset) / revenue * 100).quantize(Decimal('0.01'))

def profit(invoice_queryset, expense_queryset, *, deposits) -> Decimal:
    """All-time profit: rent collected + advance collected − expenses."""
    from apps.expenses.selectors import valid_expense_total
    return collected_amount(invoice_queryset) + (deposits.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')) - valid_expense_total(expense_queryset)

def overdue_invoices(invoice_queryset):
    today = date.today()
    candidates = invoice_queryset.filter(is_cancelled=False, due_date__lt=today)
    return [inv for inv in candidates if invoice_status(inv) == Invoice.Status.OVERDUE]

def with_computed_totals(invoice_queryset):
    item_total_sq = InvoiceItem.objects.filter(invoice=OuterRef('pk')).order_by().values('invoice').annotate(t=Sum('amount')).values('t')
    payment_total_sq = Payment.objects.filter(invoice=OuterRef('pk'), is_cancelled=False).order_by().values('invoice').annotate(t=Sum('amount')).values('t')
    return invoice_queryset.annotate(_total=Coalesce(Subquery(item_total_sq, output_field=_MONEY_FIELD), Decimal('0.00')), _paid=Coalesce(Subquery(payment_total_sq, output_field=_MONEY_FIELD), Decimal('0.00')))

def filter_by_status(invoice_queryset, status: str):
    today = date.today()
    qs = with_computed_totals(invoice_queryset)
    if status == Invoice.Status.CANCELLED:
        return qs.filter(is_cancelled=True)
    qs = qs.filter(is_cancelled=False)
    if status == Invoice.Status.PAID:
        return qs.filter(_paid__gte=F('_total'), _total__gt=0)
    unpaid = qs.filter(Q(_paid__lt=F('_total')) | Q(_total=0))
    if status == Invoice.Status.OVERDUE:
        return unpaid.filter(due_date__lt=today)
    if status == Invoice.Status.PARTIALLY_PAID:
        return unpaid.filter(_paid__gt=0).exclude(due_date__lt=today)
    if status == Invoice.Status.PENDING:
        return unpaid.filter(_paid=0).exclude(due_date__lt=today)
    return qs

def period_bounds(year: int, month: int | None = None) -> tuple[date, date]:
    """Inclusive first/last day of a calendar month, or of the whole year when month is None."""
    import calendar
    if month is None:
        return date(year, 1, 1), date(year, 12, 31)
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])

def income_payments(invoice_queryset, start: date, end: date):
    """Payments that are income of the period: rent FOR a month in the period, whenever it was collected.

    Every month is isolated — last month's rent collected today is last month's income, next month's rent
    collected today is next month's income. The collection date is only shown on the collection record.
    """
    live = invoice_queryset.filter(is_cancelled=False, billing_period_start__gte=start, billing_period_start__lte=end)
    return Payment.objects.filter(invoice__in=live, is_cancelled=False)

def income_deposits(deposit_queryset, start: date, end: date):
    """Advance (deposit) collections that are income of the period. An advance is not rent for a month, so it is
    income of the month it was collected in."""
    return deposit_queryset.filter(collected_at__gte=start, collected_at__lte=end)

def _total(qs) -> Decimal:
    return qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

def outstanding_billed_between(invoice_queryset, start: date, end: date) -> Decimal:
    """Still-unpaid balance on invoices whose billing period starts inside the period."""
    billed = invoice_queryset.filter(billing_period_start__gte=start, billing_period_start__lte=end)
    return outstanding_amount(billed)

def period_profit_loss(invoice_queryset, expense_queryset, start: date, end: date, *, deposits) -> dict:
    """The one authoritative Income − Expenses = Profit/Loss calculation for any scope and period.

    Income = rent for the period's months + advance collected in the period. `deposits` is the DepositCollection
    queryset of the same scope (required, so no page can silently leave the advance out).
    """
    from apps.expenses.selectors import unpaid, valid_expense_total
    in_period = expense_queryset.filter(date__gte=start, date__lte=end)
    live_payments = income_payments(invoice_queryset, start, end)
    advances = income_deposits(deposits, start, end)
    rent_income, advance_income = _total(live_payments), _total(advances)
    income = rent_income + advance_income
    expenses = valid_expense_total(in_period)
    from apps.expenses.models import PaidBy
    live_expenses = in_period.filter(status='active')
    by_payer = {row['paid_by']: row['total'] for row in live_expenses.order_by().values('paid_by').annotate(total=Sum('amount'))}
    unpaid_by_payer = {row['paid_by']: row['total'] for row in live_expenses.filter(paid_on__isnull=True).order_by().values('paid_by').annotate(total=Sum('amount'))}
    zero = Decimal('0.00')

    admin_collected = _total(live_payments.filter(collected_by__is_owner=True)) + _total(advances.filter(collected_by__is_owner=True))
    staff_collected = _total(live_payments.filter(collected_by__is_owner=False)) + _total(advances.filter(collected_by__is_owner=False))

    return {
        'income': income,
        'rent_income': rent_income,
        'advance_income': advance_income,
        'expenses': expenses,
        'net': income - expenses,
        'staff_collected': staff_collected,
        'admin_collected': admin_collected,
        'staff_expenses': by_payer.get(PaidBy.STAFF, zero),
        'owner_expenses': by_payer.get(PaidBy.OWNER, zero),
        'staff_expenses_unpaid': unpaid_by_payer.get(PaidBy.STAFF, zero),
        'owner_expenses_unpaid': unpaid_by_payer.get(PaidBy.OWNER, zero),
        'to_verify': valid_expense_total(live_expenses.filter(paid_on__isnull=False, verified_at__isnull=True)),
        'outstanding': outstanding_billed_between(invoice_queryset, start, end),
        'unpaid_expenses': valid_expense_total(unpaid(in_period)),
    }

def _profit_loss_by(invoice_queryset, expense_queryset, start: date, end: date, *, deposits, payment_key: str, deposit_key: str, expense_key: str) -> dict:
    """Income, expenses and net per group, using the same rules as period_profit_loss but in grouped queries."""
    from collections import defaultdict
    from apps.expenses.models import Expense
    zero = Decimal('0.00')
    income = defaultdict(lambda: zero)
    for row in income_payments(invoice_queryset, start, end).order_by().values(payment_key).annotate(total=Sum('amount')):
        income[row[payment_key]] += row['total']
    for row in income_deposits(deposits, start, end).order_by().values(deposit_key).annotate(total=Sum('amount')):
        income[row[deposit_key]] += row['total']
    expense_rows = expense_queryset.filter(status=Expense.Status.ACTIVE, date__gte=start, date__lte=end).order_by().values(expense_key).annotate(total=Sum('amount'))
    spent = {row[expense_key]: row['total'] for row in expense_rows}
    return {key: {'income': income.get(key, zero), 'expenses': spent.get(key, zero), 'net': income.get(key, zero) - spent.get(key, zero)} for key in set(income) | set(spent)}

EMPTY_PROFIT_LOSS = {
    'income': Decimal('0.00'),
    'rent_income': Decimal('0.00'),
    'advance_income': Decimal('0.00'),
    'expenses': Decimal('0.00'),
    'net': Decimal('0.00'),
    'staff_collected': Decimal('0.00'),
    'admin_collected': Decimal('0.00'),
    'staff_expenses': Decimal('0.00'),
    'owner_expenses': Decimal('0.00'),
    'staff_expenses_unpaid': Decimal('0.00'),
    'owner_expenses_unpaid': Decimal('0.00'),
    'to_verify': Decimal('0.00'),
    'outstanding': Decimal('0.00'),
    'unpaid_expenses': Decimal('0.00'),
}

def profit_loss_by_villa(invoice_queryset, expense_queryset, start: date, end: date, *, deposits) -> dict:
    return _profit_loss_by(invoice_queryset, expense_queryset, start, end, deposits=deposits, payment_key='invoice__partition__villa_id', deposit_key='tenant__partition__villa_id', expense_key='villa_id')

def profit_loss_by_partition(invoice_queryset, expense_queryset, start: date, end: date, *, deposits) -> dict:
    """Partition-level figures: income of that partition (rent + advance), and expenses explicitly recorded against it."""
    return _profit_loss_by(invoice_queryset, expense_queryset, start, end, deposits=deposits, payment_key='invoice__partition_id', deposit_key='tenant__partition_id', expense_key='partition_id')


class RentStatus:
    COLLECTED = 'collected'
    PARTIAL = 'partial'
    PENDING = 'pending'
    OVERDUE = 'overdue'

def rent_invoice_queryset(invoice_queryset, year: int, month: int):
    """The live invoice(s) whose billing period falls in or overlaps this calendar month, with computed totals."""
    start, end = period_bounds(year, month)
    return with_computed_totals(invoice_queryset.filter(is_cancelled=False, billing_period_start__lte=end, billing_period_end__gte=start))

def unbilled_charges_by_partition(partition_pks, year: int, month: int, tenant_by_partition=None):
    from collections import defaultdict
    details = unbilled_charges_details_by_partition(partition_pks, year, month, tenant_by_partition)
    res = defaultdict(lambda: Decimal('0.00'))
    for pid, items in details.items():
        res[pid] = sum((c['amount'] for c in items), Decimal('0.00'))
    return res

def unbilled_charges_details_by_partition(partition_pks, year: int, month: int, tenant_by_partition=None):
    from collections import defaultdict
    start, end = period_bounds(year, month)
    live_items = InvoiceItem.objects.filter(invoice__is_cancelled=False)
    overlapping_items = live_items.filter(
        invoice__billing_period_start__lte=end,
        invoice__billing_period_end__gte=start
    )
    unbilled_qs = Charge.objects.filter(
        partition_id__in=partition_pks,
        is_active=True,
        start_date__lte=end
    ).exclude(end_date__lt=start).exclude(
        frequency=Charge.Frequency.ONE_TIME,
        invoice_items__in=live_items
    ).exclude(
        invoice_items__in=overlapping_items
    ).select_related('charge_type').distinct()
    res = defaultdict(list)
    for c in unbilled_qs:
        if tenant_by_partition:
            t = tenant_by_partition.get(c.partition_id)
            if t:
                if c.end_date and c.end_date < t.move_in_date:
                    continue
                if t.move_out_date and c.start_date > t.move_out_date:
                    continue
        name = c.description or c.charge_type.name
        res[c.partition_id].append({'name': name, 'amount': c.amount})
    return res

def prior_unpaid_invoices_by_partition(partition_pks, start_date, tenant_by_partition=None):
    from collections import defaultdict
    prior_invs = Invoice.objects.filter(
        partition_id__in=partition_pks,
        is_cancelled=False,
        billing_period_end__lt=start_date
    ).prefetch_related('items', 'payments').order_by('billing_period_start')
    res = defaultdict(list)
    for inv in prior_invs:
        total = sum((item.amount for item in inv.items.all()), start=Decimal('0.00'))
        paid = sum((p.amount for p in inv.payments.all() if not p.is_cancelled), start=Decimal('0.00'))
        outstanding = total - paid
        if outstanding > 0:
            charges = [{'name': item.description, 'amount': item.amount} for item in inv.items.all()]
            res[(inv.partition_id, inv.tenant_id)].append({
                'id': inv.pk,
                'period': inv.billing_period_start.strftime('%B %Y'),
                'total': total,
                'paid': paid,
                'outstanding': outstanding,
                'charges': charges,
            })
    if tenant_by_partition:
        for pid, tenant in tenant_by_partition.items():
            cur = first_billed_month(tenant)
            while cur < start_date:
                period = cur.strftime('%B %Y')
                for c in unbilled_charges_details_by_partition([pid], cur.year, cur.month, {pid: tenant}).get(pid, []):
                    entry = next((i for i in res[(pid, tenant.pk)] if i['period'] == period), None)
                    if not entry:
                        entry = {'id': None, 'period': period, 'total': Decimal('0.00'), 'paid': Decimal('0.00'), 'outstanding': Decimal('0.00'), 'charges': []}
                        res[(pid, tenant.pk)].append(entry)
                    entry['total'] += c['amount']
                    entry['outstanding'] += c['amount']
                    entry['charges'].append(c)
                cur = (cur + timedelta(days=32)).replace(day=1)
    return res

def rent_schedule(partition, today=None) -> dict:
    """The rent in force today and any scheduled change, from the partition's Rent charges (what is actually billed)."""
    return rent_schedule_from(Charge.objects.filter(partition=partition, is_active=True, charge_type__name='Rent'), today)

def rent_schedules_by_partition(partition_ids, today=None) -> dict:
    from collections import defaultdict
    grouped = defaultdict(list)
    for charge in Charge.objects.filter(partition_id__in=list(partition_ids), is_active=True, charge_type__name='Rent'):
        grouped[charge.partition_id].append(charge)
    return {pid: rent_schedule_from(charges, today) for pid, charges in grouped.items()}

def rent_schedule_from(charges, today=None) -> dict:
    today = today or date.today()
    charges = sorted(charges, key=lambda c: c.start_date)
    current = next((c for c in charges if c.start_date <= today and (c.end_date is None or c.end_date >= today)), None)
    upcoming = next((c for c in charges if c.start_date > today), None)
    return {'current': current.amount if current else None, 'upcoming': upcoming.amount if upcoming else None, 'upcoming_from': upcoming.start_date if upcoming else None}

def first_billed_month(tenant) -> date:
    """Rent is tracked from the month the tenant was added to the system (or moved in, if later), so onboarding a
    long-standing tenant never invents years of arrears."""
    from django.utils import timezone
    added = timezone.localdate(tenant.created_at) if tenant.created_at else date.today()
    return max(tenant.move_in_date, added).replace(day=1)

def resident_in_month(tenant, year: int, month: int) -> bool:
    start, end = period_bounds(year, month)
    return tenant.move_in_date <= end and (tenant.move_out_date is None or tenant.move_out_date >= start)

def rent_sheet(*, tenants, invoice_queryset, year: int, month: int, today: date):
    """One month's rent sheet: a row for every tenant who lived there during the month — including one who has
    since moved out — with what is due, collected (and when), and still pending. Rows sum to the month's income."""
    from collections import defaultdict
    from apps.tenancy.models import Tenant
    start, end = period_bounds(year, month)
    tenants = sorted((t for t in tenants if t.status != Tenant.Status.ARCHIVED and resident_in_month(t, year, month)), key=lambda t: (t.partition.name, t.move_in_date, t.pk))
    current_by_partition = {t.partition_id: t for t in tenants if t.status == Tenant.Status.ACTIVE}
    partition_pks = list({t.partition_id for t in tenants})
    invoices_by_partition_tenant = defaultdict(list)
    for invoice in rent_invoice_queryset(invoice_queryset, year, month).prefetch_related('items', 'payments__collected_by'):
        invoices_by_partition_tenant[(invoice.partition_id, invoice.tenant_id)].append(invoice)
    unbilled_map = unbilled_charges_by_partition(partition_pks, year, month, current_by_partition)
    unbilled_details_map = unbilled_charges_details_by_partition(partition_pks, year, month, current_by_partition)
    prior_unpaid_map = prior_unpaid_invoices_by_partition(partition_pks, start, current_by_partition)
    zero = Decimal('0.00')
    rows, expected, collected = [], zero, zero
    for tenant in tenants:
        partition = tenant.partition
        is_current = tenant.status == Tenant.Status.ACTIVE
        p_invoices = invoices_by_partition_tenant.get((partition.pk, tenant.pk), [])
        if not p_invoices and (not is_current or start < first_billed_month(tenant)):
            continue  # nothing billed, and either a former tenant or a month before rent tracking began
        # Unbilled charges belong to whoever lives there now; a former tenant's months were billed at move-out.
        unbilled = unbilled_map[partition.pk] if is_current else zero
        charges_breakdown = []
        live_payments = []
        staff_collected = zero
        admin_collected = zero
        if p_invoices:
            due = sum((inv._total for inv in p_invoices), start=zero) + unbilled
            paid = sum((inv._paid for inv in p_invoices), start=zero)
            primary_invoice = p_invoices[0]
            for inv in p_invoices:
                for item in inv.items.all():
                    charges_breakdown.append({'name': item.description, 'amount': item.amount})
                for p in inv.payments.all():
                    if not p.is_cancelled:
                        live_payments.append(p)
                        if p.collected_by and p.collected_by.is_owner:
                            admin_collected += p.amount
                        elif p.collected_by:
                            staff_collected += p.amount
            for c in (unbilled_details_map.get(partition.pk, []) if is_current else []):
                charges_breakdown.append(c)
            if due > 0 and paid >= due:
                status = RentStatus.COLLECTED
            elif paid > 0:
                status = RentStatus.PARTIAL
            elif any(inv.due_date < today and inv._paid < inv._total for inv in p_invoices):
                status = RentStatus.OVERDUE
            else:
                status = RentStatus.PENDING
        else:
            primary_invoice = None
            due = unbilled if unbilled > 0 else tenant.monthly_rent
            paid = zero
            status = RentStatus.PENDING
            unbilled_items = unbilled_details_map.get(partition.pk, [])
            if unbilled_items:
                charges_breakdown.extend(unbilled_items)
            else:
                charges_breakdown.append({'name': 'Rent', 'amount': tenant.monthly_rent})
        prior_unpaid = prior_unpaid_map.get((partition.pk, tenant.pk), [])
        prior_unpaid_total = sum((p['outstanding'] for p in prior_unpaid), start=zero)
        rent_amount = tenant.monthly_rent
        for item in charges_breakdown:
            if item.get('name') == 'Rent':
                rent_amount = item.get('amount', tenant.monthly_rent)
                break
        collector_users = list({p.collected_by for p in live_payments if p.collected_by})
        collector_user_ids = {p.collected_by_id for p in live_payments if p.collected_by_id}
        breakdown_by_user = {}
        for p in live_payments:
            if p.collected_by:
                uid = p.collected_by_id
                if uid not in breakdown_by_user:
                    breakdown_by_user[uid] = {
                        'user': p.collected_by,
                        'amount': zero,
                        'is_owner': p.collected_by.is_owner,
                        'role': 'Admin' if p.collected_by.is_owner else 'Staff',
                        'dates': [],
                    }
                breakdown_by_user[uid]['amount'] += p.amount
                breakdown_by_user[uid]['dates'].append(p.collected_at)
        collector_breakdown = list(breakdown_by_user.values())
        for cb in collector_breakdown:
            cb['dates'] = sorted(set(cb['dates']))
        live_payments.sort(key=lambda p: (p.collected_at, p.pk))

        rows.append({
            'partition': partition,
            'tenant': tenant,
            'is_current': is_current,
            'invoice': primary_invoice,
            'due': due,
            'paid': paid,
            'balance': due - paid,
            'status': status,
            'charges': charges_breakdown,
            'prior_unpaid': prior_unpaid,
            'prior_unpaid_total': prior_unpaid_total,
            'rent_amount': rent_amount,
            'payments': live_payments,
            'staff_collected': staff_collected,
            'admin_collected': admin_collected,
            'collector_users': collector_users,
            'collector_user_ids': collector_user_ids,
            'collector_breakdown': collector_breakdown,
        })
        expected += due
        collected += paid
    return rows, {
        'expected': expected,
        'collected': collected,
        'staff_collected': sum((r['staff_collected'] for r in rows), zero),
        'admin_collected': sum((r['admin_collected'] for r in rows), zero),
        'pending': expected - collected,
        'pending_count': sum(1 for r in rows if r['status'] != RentStatus.COLLECTED),
    }

def pending_rent_by_villa(invoice_queryset, partition_queryset, year: int, month: int) -> dict:
    """villa id -> number of occupied partitions whose rent for the month is not fully collected."""
    _, month_end = period_bounds(year, month)
    occupied = partition_queryset.filter(tenancies__status='active', tenancies__move_in_date__lte=month_end).values_list('pk', 'villa_id').distinct()
    occupied_by_villa = {}
    partition_villa = {}
    for partition_id, villa_id in occupied:
        partition_villa[partition_id] = villa_id
        occupied_by_villa[villa_id] = occupied_by_villa.get(villa_id, 0) + 1
    unbilled_map = unbilled_charges_by_partition(list(partition_villa), year, month)
    paid_partitions = set()
    for invoice in rent_invoice_queryset(invoice_queryset.filter(partition__in=list(partition_villa)), year, month):
        if invoice._total > 0 and invoice._paid >= invoice._total:
            paid_partitions.add(invoice.partition_id)
    for partition_id, unbilled_amt in unbilled_map.items():
        if unbilled_amt > 0:
            paid_partitions.discard(partition_id)
    paid_by_villa = {}
    for partition_id in paid_partitions:
        villa_id = partition_villa[partition_id]
        paid_by_villa[villa_id] = paid_by_villa.get(villa_id, 0) + 1
    return {villa_id: max(0, count - paid_by_villa.get(villa_id, 0)) for villa_id, count in occupied_by_villa.items()}
