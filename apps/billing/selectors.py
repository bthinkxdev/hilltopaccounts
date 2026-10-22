from datetime import date
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

def profit(invoice_queryset, expense_queryset) -> Decimal:
    from apps.expenses.selectors import valid_expense_total
    return collected_amount(invoice_queryset) - valid_expense_total(expense_queryset)

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

def income_between(invoice_queryset, start: date, end: date) -> Decimal:
    """Cash-basis income: live payments collected in the period against non-cancelled invoices."""
    live = invoice_queryset.filter(is_cancelled=False)
    return Payment.objects.filter(invoice__in=live, is_cancelled=False, collected_at__gte=start, collected_at__lte=end).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

def outstanding_billed_between(invoice_queryset, start: date, end: date) -> Decimal:
    """Still-unpaid balance on invoices whose billing period starts inside the period."""
    billed = invoice_queryset.filter(billing_period_start__gte=start, billing_period_start__lte=end)
    return outstanding_amount(billed)

def period_profit_loss(invoice_queryset, expense_queryset, start: date, end: date) -> dict:
    """The one authoritative Income − Expenses = Profit/Loss calculation for any scope and period."""
    from apps.expenses.selectors import unpaid, valid_expense_total
    in_period = expense_queryset.filter(date__gte=start, date__lte=end)
    income = income_between(invoice_queryset, start, end)
    expenses = valid_expense_total(in_period)
    from apps.expenses.models import PaidBy
    live_expenses = in_period.filter(status='active')
    by_payer = {row['paid_by']: row['total'] for row in live_expenses.order_by().values('paid_by').annotate(total=Sum('amount'))}
    zero = Decimal('0.00')

    live_invoices = invoice_queryset.filter(is_cancelled=False)
    live_payments = Payment.objects.filter(invoice__in=live_invoices, is_cancelled=False, collected_at__gte=start, collected_at__lte=end)
    admin_collected = live_payments.filter(collected_by__is_owner=True).aggregate(total=Sum('amount'))['total'] or zero
    staff_collected = live_payments.filter(collected_by__is_owner=False).aggregate(total=Sum('amount'))['total'] or zero

    return {
        'income': income,
        'expenses': expenses,
        'net': income - expenses,
        'staff_collected': staff_collected,
        'admin_collected': admin_collected,
        'staff_expenses': by_payer.get(PaidBy.STAFF, zero),
        'owner_expenses': by_payer.get(PaidBy.OWNER, zero),
        'to_verify': valid_expense_total(live_expenses.filter(paid_on__isnull=False, verified_at__isnull=True)),
        'outstanding': outstanding_billed_between(invoice_queryset, start, end),
        'unpaid_expenses': valid_expense_total(unpaid(in_period)),
    }

def _profit_loss_by(invoice_queryset, expense_queryset, start: date, end: date, *, payment_key: str, expense_key: str) -> dict:
    """Income, expenses and net per group, using the same rules as period_profit_loss but in two grouped queries."""
    from apps.expenses.models import Expense
    live = invoice_queryset.filter(is_cancelled=False)
    income_rows = Payment.objects.filter(invoice__in=live, is_cancelled=False, collected_at__gte=start, collected_at__lte=end).order_by().values(payment_key).annotate(total=Sum('amount'))
    expense_rows = expense_queryset.filter(status=Expense.Status.ACTIVE, date__gte=start, date__lte=end).order_by().values(expense_key).annotate(total=Sum('amount'))
    income = {row[payment_key]: row['total'] for row in income_rows}
    spent = {row[expense_key]: row['total'] for row in expense_rows}
    zero = Decimal('0.00')
    return {key: {'income': income.get(key, zero), 'expenses': spent.get(key, zero), 'net': income.get(key, zero) - spent.get(key, zero)} for key in set(income) | set(spent)}

EMPTY_PROFIT_LOSS = {
    'income': Decimal('0.00'),
    'expenses': Decimal('0.00'),
    'net': Decimal('0.00'),
    'staff_collected': Decimal('0.00'),
    'admin_collected': Decimal('0.00'),
    'staff_expenses': Decimal('0.00'),
    'owner_expenses': Decimal('0.00'),
    'to_verify': Decimal('0.00'),
    'outstanding': Decimal('0.00'),
    'unpaid_expenses': Decimal('0.00'),
}

def profit_loss_by_villa(invoice_queryset, expense_queryset, start: date, end: date) -> dict:
    return _profit_loss_by(invoice_queryset, expense_queryset, start, end, payment_key='invoice__partition__villa_id', expense_key='villa_id')

def profit_loss_by_partition(invoice_queryset, expense_queryset, start: date, end: date) -> dict:
    """Partition-level figures: income billed to that partition, and expenses explicitly recorded against it."""
    return _profit_loss_by(invoice_queryset, expense_queryset, start, end, payment_key='invoice__partition_id', expense_key='partition_id')


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

def prior_unpaid_invoices_by_partition(partition_pks, start_date):
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
    return res

def rent_sheet(*, partitions, tenant_by_partition: dict, invoice_queryset, year: int, month: int, today: date):
    """Month-end collection sheet for one villa: per occupied partition, what is due, collected and still pending."""
    from collections import defaultdict
    start, end = period_bounds(year, month)
    invoices_by_partition_tenant = defaultdict(list)
    for invoice in rent_invoice_queryset(invoice_queryset, year, month).prefetch_related('items', 'payments__collected_by'):
        invoices_by_partition_tenant[(invoice.partition_id, invoice.tenant_id)].append(invoice)
    unbilled_map = unbilled_charges_by_partition([p.pk for p in partitions], year, month, tenant_by_partition)
    unbilled_details_map = unbilled_charges_details_by_partition([p.pk for p in partitions], year, month, tenant_by_partition)
    prior_unpaid_map = prior_unpaid_invoices_by_partition([p.pk for p in partitions], start)
    zero = Decimal('0.00')
    rows, expected, collected = [], zero, zero
    for partition in partitions:
        tenant = tenant_by_partition.get(partition.pk)
        if tenant is None:
            continue
        p_invoices = invoices_by_partition_tenant.get((partition.pk, tenant.pk), [])
        unbilled = unbilled_map[partition.pk]
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
            for c in unbilled_details_map.get(partition.pk, []):
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
                    }
                breakdown_by_user[uid]['amount'] += p.amount
        collector_breakdown = list(breakdown_by_user.values())

        rows.append({
            'partition': partition,
            'tenant': tenant,
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
    return rows, {'expected': expected, 'collected': collected, 'pending': expected - collected, 'pending_count': sum(1 for r in rows if r['status'] != RentStatus.COLLECTED)}

def pending_rent_by_villa(invoice_queryset, partition_queryset, year: int, month: int) -> dict:
    """villa id -> number of occupied partitions whose rent for the month is not fully collected."""
    occupied = partition_queryset.filter(tenancies__status='active').values_list('pk', 'villa_id').distinct()
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
