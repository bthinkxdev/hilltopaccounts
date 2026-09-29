from decimal import Decimal
from django.db.models import Sum
from .models import Expense

def valid_expense_total(expense_queryset) -> Decimal:
    return expense_queryset.filter(status=Expense.Status.ACTIVE).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
