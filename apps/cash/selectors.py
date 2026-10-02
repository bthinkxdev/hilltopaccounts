from decimal import Decimal
from django.db.models import Sum
from apps.billing.models import Payment
from apps.expenses.models import Expense, PaidBy
from .models import CashHandover

def unclaimed_cash_payments(staff):
    return Payment.objects.filter(collected_by=staff, method=Payment.Method.CASH, is_cancelled=False).exclude(handovers__status__in=[CashHandover.Status.SUBMITTED, CashHandover.Status.CONFIRMED])

def total_collected(staff) -> Decimal:
    return Payment.objects.filter(collected_by=staff, method=Payment.Method.CASH, is_cancelled=False).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

def total_confirmed_handed_over(staff) -> Decimal:
    return CashHandover.objects.filter(staff=staff, status=CashHandover.Status.CONFIRMED).aggregate(total=Sum('confirmed_amount'))['total'] or Decimal('0.00')

def pending_handover_amount(staff) -> Decimal:
    return CashHandover.objects.filter(staff=staff, status=CashHandover.Status.SUBMITTED).aggregate(total=Sum('declared_amount'))['total'] or Decimal('0.00')

def staff_paid_expenses(staff):
    """Expenses this staff member settled out of the cash they collected."""
    return Expense.objects.filter(status=Expense.Status.ACTIVE, paid_by=PaidBy.STAFF, paid_by_user=staff, paid_on__isnull=False)

def unclaimed_staff_expenses(staff):
    return staff_paid_expenses(staff).exclude(handovers__status__in=[CashHandover.Status.SUBMITTED, CashHandover.Status.CONFIRMED])

def total_staff_paid_expenses(staff) -> Decimal:
    return staff_paid_expenses(staff).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

def outstanding_cash_for(staff) -> Decimal:
    """Cash the staff member still holds: collected, minus what they spent on owner business, minus what the owner confirmed."""
    return total_collected(staff) - total_staff_paid_expenses(staff) - total_confirmed_handed_over(staff)


def staff_accountability(viewer, year, month) -> list[dict]:
    """One row per staff member the viewer oversees: what they collected, spent, handed over and still hold.

    Amounts are limited to the viewer's own villas, and handovers to those the viewer may see. Grouped queries only.
    """
    from apps.accounts import selectors as access
    from apps.accounts.models import Assignment, User
    from apps.billing import selectors as billing_selectors
    from apps.villas.models import Partition
    scope = access.financial_villas(viewer)
    assignments = list(Assignment.objects.filter(role=Assignment.Role.VILLA_STAFF, villa__in=scope).select_related('user', 'villa').order_by('villa__name'))
    staff_ids = {a.user_id for a in assignments}
    if not staff_ids:
        return []
    villas_by_staff = {}
    for a in assignments:
        villas_by_staff.setdefault(a.user_id, []).append(a.villa)
    collected = {r['collected_by']: r['total'] for r in Payment.objects.filter(collected_by__in=staff_ids, method=Payment.Method.CASH, is_cancelled=False, invoice__partition__villa__in=scope).order_by().values('collected_by').annotate(total=Sum('amount'))}
    spent = {r['paid_by_user']: r['total'] for r in Expense.objects.filter(status=Expense.Status.ACTIVE, paid_by=PaidBy.STAFF, paid_by_user__in=staff_ids, paid_on__isnull=False, villa__in=scope).order_by().values('paid_by_user').annotate(total=Sum('amount'))}
    handovers = access.cash_handovers_visible_to(viewer).filter(staff__in=staff_ids).order_by().values('staff', 'status').annotate(declared=Sum('declared_amount'), confirmed=Sum('confirmed_amount'))
    confirmed, pending = {}, {}
    for row in handovers:
        if row['status'] == CashHandover.Status.CONFIRMED:
            confirmed[row['staff']] = row['confirmed'] or Decimal('0.00')
        elif row['status'] == CashHandover.Status.SUBMITTED:
            pending[row['staff']] = row['declared'] or Decimal('0.00')
    pending_rent = billing_selectors.pending_rent_by_villa(access.invoices_visible_to(viewer).filter(partition__villa__in=scope), Partition.objects.filter(villa__in=scope), year, month)
    zero = Decimal('0.00')
    rows = []
    for user in User.objects.filter(pk__in=staff_ids).order_by('first_name', 'username'):
        villas = villas_by_staff[user.pk]
        rows.append({'staff': user, 'villas': villas, 'collected': collected.get(user.pk, zero), 'expenses_paid': spent.get(user.pk, zero), 'handed_over': confirmed.get(user.pk, zero), 'awaiting_confirmation': pending.get(user.pk, zero), 'holding': collected.get(user.pk, zero) - spent.get(user.pk, zero) - confirmed.get(user.pk, zero), 'pending_rent_count': sum(pending_rent.get(v.pk, 0) for v in villas)})
    return rows


def villa_cash_position(villa) -> dict:
    """Where the cash collected in one villa is: still with staff, handed over, or spent on its expenses.

    holding = cash collected − expenses staff paid − what the owner has confirmed receiving (net of those expenses).
    """
    zero = Decimal('0.00')
    in_villa = Payment.objects.filter(method=Payment.Method.CASH, is_cancelled=False, invoice__partition__villa=villa)
    collected = in_villa.aggregate(t=Sum('amount'))['t'] or zero
    spent = Expense.objects.filter(villa=villa, status=Expense.Status.ACTIVE, paid_by=PaidBy.STAFF, paid_on__isnull=False).aggregate(t=Sum('amount'))['t'] or zero

    def handed(status):
        cash = Payment.objects.filter(handovers__status=status, invoice__partition__villa=villa).aggregate(t=Sum('amount'))['t'] or zero
        netted = Expense.objects.filter(handovers__status=status, villa=villa).aggregate(t=Sum('amount'))['t'] or zero
        return cash - netted

    received = handed(CashHandover.Status.CONFIRMED)
    awaiting = handed(CashHandover.Status.SUBMITTED)
    return {'collected': collected, 'spent_by_staff': spent, 'received_by_owner': received, 'awaiting_confirmation': awaiting, 'holding': collected - spent - received}
