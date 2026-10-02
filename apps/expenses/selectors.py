from datetime import date, timedelta
from decimal import Decimal
from django.db.models import Count, Q, Sum
from .models import Expense, ExpenseCategory

DUE_SOON_DAYS = 7

class DueBucket:
    OVERDUE = 'overdue'
    TODAY = 'today'
    UPCOMING = 'upcoming'
    PAID = 'paid'
    UNPAID = 'unpaid'
    VERIFY = 'verify'
    CHOICES = [(OVERDUE, 'Overdue'), (TODAY, 'Due today'), (UPCOMING, 'Upcoming'), (UNPAID, 'Outstanding'), (VERIFY, 'To verify'), (PAID, 'Paid')]

def valid_expense_total(expense_queryset) -> Decimal:
    return expense_queryset.filter(status=Expense.Status.ACTIVE).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

def active(expense_queryset):
    return expense_queryset.filter(status=Expense.Status.ACTIVE)

def unpaid(expense_queryset):
    return active(expense_queryset).filter(paid_on__isnull=True)

def filter_by_due_bucket(expense_queryset, bucket, today=None):
    today = today or date.today()
    if bucket == DueBucket.PAID:
        return active(expense_queryset).filter(paid_on__isnull=False)
    if bucket == DueBucket.VERIFY:
        return active(expense_queryset).filter(paid_on__isnull=False, verified_at__isnull=True)
    if bucket == DueBucket.UNPAID:
        return unpaid(expense_queryset)
    if bucket == DueBucket.OVERDUE:
        return unpaid(expense_queryset).filter(due_date__lt=today)
    if bucket == DueBucket.TODAY:
        return unpaid(expense_queryset).filter(due_date=today)
    if bucket == DueBucket.UPCOMING:
        return unpaid(expense_queryset).filter(due_date__gt=today)
    return expense_queryset

def due_state(expense: Expense, today=None) -> str:
    """Label a single row for display: paid / overdue / today / upcoming / unscheduled / inactive."""
    today = today or date.today()
    if expense.status != Expense.Status.ACTIVE:
        return expense.status
    if expense.paid_on is not None:
        return DueBucket.VERIFY if expense.verified_at is None else DueBucket.PAID
    if expense.due_date is None:
        return DueBucket.UNPAID
    if expense.due_date < today:
        return DueBucket.OVERDUE
    if expense.due_date == today:
        return DueBucket.TODAY
    return DueBucket.UPCOMING

def _bucket_q(bucket, today) -> Q:
    live = Q(status=Expense.Status.ACTIVE)
    unpaid_q = live & Q(paid_on__isnull=True)
    return {
        DueBucket.PAID: live & Q(paid_on__isnull=False),
        DueBucket.VERIFY: live & Q(paid_on__isnull=False, verified_at__isnull=True),
        DueBucket.UNPAID: unpaid_q,
        DueBucket.OVERDUE: unpaid_q & Q(due_date__lt=today),
        DueBucket.TODAY: unpaid_q & Q(due_date=today),
        DueBucket.UPCOMING: unpaid_q & Q(due_date__gt=today),
    }[bucket]

def due_summary(expense_queryset, today=None) -> dict:
    """Counts and totals for every accountability bucket, in one aggregate query."""
    today = today or date.today()
    aggregates = {}
    for bucket, _label in DueBucket.CHOICES:
        q = _bucket_q(bucket, today)
        aggregates[f'{bucket}_count'] = Count('pk', filter=q)
        aggregates[f'{bucket}_total'] = Sum('amount', filter=q)
    row = expense_queryset.aggregate(**aggregates)
    return {bucket: {'count': row[f'{bucket}_count'], 'total': row[f'{bucket}_total'] or Decimal('0.00')} for bucket, _label in DueBucket.CHOICES}

def used_expense_names(expense_queryset):
    names = expense_queryset.values_list('category__name', flat=True).distinct().order_by('category__name')
    return list(names)

def latest_amount_for(expense_queryset, *, name, villa=None):
    """Most recent amount recorded under this expense name, preferring the same villa. None when never used."""
    base = active(expense_queryset).filter(category__name__iexact=name.strip()).order_by('-date', '-id')
    if villa is not None:
        same_villa = base.filter(villa=villa).values_list('amount', flat=True).first()
        if same_villa is not None:
            return same_villa
    return base.values_list('amount', flat=True).first()

class FixedStatus:
    PAID = 'paid'
    TO_VERIFY = 'to_verify'
    UNPAID = 'unpaid'
    DUE_TODAY = 'due_today'
    OVERDUE = 'overdue'
    NOT_CREATED = 'not_created'
    CANCELLED = 'cancelled'

def fixed_expense_checklist(villa, year: int, month: int, today=None):
    """The villa's fixed expenses for one month, each with its paid / unpaid / overdue state.

    A fixed expense that has not been generated for the month still appears (as 'not_created', with the amount and
    due date it will get), so every month shows the full list and nothing can be silently missing.
    """
    import calendar
    from datetime import date as date_type
    from .models import RecurringExpense
    today = today or date.today()
    period = date_type(year, month, 1)
    generated = {e.recurring_id: e for e in Expense.objects.filter(villa=villa, period=period, recurring__isnull=False).select_related('category', 'recurring')}
    templates = {t.pk: t for t in RecurringExpense.objects.filter(villa=villa).filter(pk__in=list(generated)).select_related('category')}
    for template in RecurringExpense.objects.filter(villa=villa, is_active=True).select_related('category'):
        templates[template.pk] = template
    zero = Decimal('0.00')
    rows, totals = [], {'total': zero, 'paid': zero, 'unpaid': zero, 'unpaid_count': 0, 'missing_count': 0, 'to_verify_count': 0}
    last_day = calendar.monthrange(year, month)[1]
    for template in sorted(templates.values(), key=lambda t: (t.due_day, t.category.name)):
        expense = generated.get(template.pk)
        if expense is None:
            due_date = date_type(year, month, min(template.due_day, last_day))
            status, amount = FixedStatus.NOT_CREATED, template.amount
            totals['missing_count'] += 1
        else:
            due_date, amount = expense.due_date, expense.amount
            if expense.status != Expense.Status.ACTIVE:
                status = FixedStatus.CANCELLED
            elif expense.paid_on is not None:
                status = FixedStatus.PAID if expense.verified_at is not None else FixedStatus.TO_VERIFY
            elif due_date < today:
                status = FixedStatus.OVERDUE
            elif due_date == today:
                status = FixedStatus.DUE_TODAY
            else:
                status = FixedStatus.UNPAID
        if status != FixedStatus.CANCELLED:
            totals['total'] += amount
            if status in (FixedStatus.PAID, FixedStatus.TO_VERIFY):
                totals['paid'] += amount
                if status == FixedStatus.TO_VERIFY:
                    totals['to_verify_count'] += 1
            else:
                totals['unpaid'] += amount
                totals['unpaid_count'] += 1
        rows.append({'template': template, 'expense': expense, 'amount': amount, 'due_date': due_date, 'status': status})
    return rows, totals

def fixed_pending_by_villa(villa_ids, year: int, month: int) -> dict:
    """villa id -> number of active fixed expenses not yet paid (or not yet created) for the month."""
    from datetime import date as date_type
    from .models import RecurringExpense
    period = date_type(year, month, 1)
    wanted = {}
    for villa_id, pk in RecurringExpense.objects.filter(villa_id__in=villa_ids, is_active=True).values_list('villa_id', 'pk'):
        wanted.setdefault(villa_id, set()).add(pk)
    settled = {}
    for villa_id, recurring_id in Expense.objects.filter(villa_id__in=villa_ids, period=period, recurring__isnull=False).filter(Q(paid_on__isnull=False) | ~Q(status=Expense.Status.ACTIVE)).values_list('villa_id', 'recurring_id'):
        settled.setdefault(villa_id, set()).add(recurring_id)
    return {villa_id: len(pks - settled.get(villa_id, set())) for villa_id, pks in wanted.items()}
