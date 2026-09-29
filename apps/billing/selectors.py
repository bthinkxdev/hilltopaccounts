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
