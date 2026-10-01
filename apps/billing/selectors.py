from datetime import date
from decimal import Decimal
from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from .models import Invoice, InvoiceItem, Payment
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
    return {'income': income, 'expenses': expenses, 'outstanding': outstanding_billed_between(invoice_queryset, start, end), 'unpaid_expenses': valid_expense_total(unpaid(in_period)), 'net': income - expenses}

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

EMPTY_PROFIT_LOSS = {'income': Decimal('0.00'), 'expenses': Decimal('0.00'), 'net': Decimal('0.00')}

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
    """The live invoice(s) whose billing period is exactly this calendar month, with computed totals."""
    start, end = period_bounds(year, month)
    return with_computed_totals(invoice_queryset.filter(is_cancelled=False, billing_period_start=start, billing_period_end=end))

def rent_sheet(*, partitions, tenant_by_partition: dict, invoice_queryset, year: int, month: int, today: date):
    """Month-end collection sheet for one villa: per occupied partition, what is due, collected and still pending."""
    invoices = {invoice.partition_id: invoice for invoice in rent_invoice_queryset(invoice_queryset, year, month)}
    zero = Decimal('0.00')
    rows, expected, collected = [], zero, zero
    for partition in partitions:
        tenant = tenant_by_partition.get(partition.pk)
        if tenant is None:
            continue
        invoice = invoices.get(partition.pk)
        due = invoice._total if invoice else tenant.monthly_rent
        paid = invoice._paid if invoice else zero
        if invoice and due > 0 and paid >= due:
            status = RentStatus.COLLECTED
        elif paid > 0:
            status = RentStatus.PARTIAL
        elif invoice and invoice.due_date < today:
            status = RentStatus.OVERDUE
        else:
            status = RentStatus.PENDING
        rows.append({'partition': partition, 'tenant': tenant, 'invoice': invoice, 'due': due, 'paid': paid, 'balance': due - paid, 'status': status})
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
    paid_up = {}
    for invoice in rent_invoice_queryset(invoice_queryset.filter(partition__in=list(partition_villa)), year, month):
        if invoice._total > 0 and invoice._paid >= invoice._total:
            villa_id = partition_villa[invoice.partition_id]
            paid_up[villa_id] = paid_up.get(villa_id, 0) + 1
    return {villa_id: count - paid_up.get(villa_id, 0) for villa_id, count in occupied_by_villa.items()}
