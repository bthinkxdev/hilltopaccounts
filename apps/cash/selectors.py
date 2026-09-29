from decimal import Decimal
from django.db.models import Sum
from apps.billing.models import Payment
from .models import CashHandover

def unclaimed_cash_payments(staff):
    return Payment.objects.filter(collected_by=staff, method=Payment.Method.CASH, is_cancelled=False).exclude(handovers__status__in=[CashHandover.Status.SUBMITTED, CashHandover.Status.CONFIRMED])

def total_collected(staff) -> Decimal:
    return Payment.objects.filter(collected_by=staff, method=Payment.Method.CASH, is_cancelled=False).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

def total_confirmed_handed_over(staff) -> Decimal:
    return CashHandover.objects.filter(staff=staff, status=CashHandover.Status.CONFIRMED).aggregate(total=Sum('confirmed_amount'))['total'] or Decimal('0.00')

def pending_handover_amount(staff) -> Decimal:
    return CashHandover.objects.filter(staff=staff, status=CashHandover.Status.SUBMITTED).aggregate(total=Sum('declared_amount'))['total'] or Decimal('0.00')

def outstanding_cash_for(staff) -> Decimal:
    return total_collected(staff) - total_confirmed_handed_over(staff)
