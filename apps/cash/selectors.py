from decimal import Decimal
from django.db.models import Q, Sum
from apps.billing.models import Payment
from apps.expenses.models import Expense, PaidBy
from .models import CashHandover

def _cash_deposits():
    from apps.tenancy.models import DepositCollection
    return DepositCollection.objects.filter(method=DepositCollection.Method.CASH)

def unclaimed_cash_payments(staff):
    return Payment.objects.filter(collected_by=staff, method=Payment.Method.CASH, is_cancelled=False).exclude(handovers__status__in=[CashHandover.Status.SUBMITTED, CashHandover.Status.CONFIRMED])

def unclaimed_cash_deposits(staff):
    """Advance cash this staff member collected and has not yet handed over."""
    return _cash_deposits().filter(collected_by=staff).exclude(handovers__status__in=[CashHandover.Status.SUBMITTED, CashHandover.Status.CONFIRMED])

def total_collected(staff) -> Decimal:
    """All cash in the staff member's hands from collections: rent and advance."""
    rent = Payment.objects.filter(collected_by=staff, method=Payment.Method.CASH, is_cancelled=False).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    return rent + (_cash_deposits().filter(collected_by=staff).aggregate(total=Sum('amount'))['total'] or Decimal('0.00'))

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
    """Cash the staff member still holds: collected, minus what they spent on admin business, minus what the admin confirmed."""
    return total_collected(staff) - total_staff_paid_expenses(staff) - total_confirmed_handed_over(staff)


def staff_accountability(viewer, year, month) -> list[dict]:
    """One row per staff member the viewer oversees: what they collected, spent, handed over and still hold.

    Amounts are limited to the viewer's own villas, and handovers to those the viewer may see. Grouped queries only.
    """
    from apps.accounts import selectors as access
    from apps.accounts.models import Assignment, User
    from apps.billing import selectors as billing_selectors
    from apps.villas.models import Partition
    from apps.villas.models import Villa
    scope = access.financial_villas(viewer)
    assignments = list(Assignment.objects.filter(role=Assignment.Role.VILLA_STAFF, villa__in=scope).select_related('user', 'villa').order_by('villa__name'))
    villas_by_staff = {}
    for a in assignments:
        villas_by_staff.setdefault(a.user_id, []).append(a.villa)
    # Anyone who is not the admin and collected cash here must hand it over (manager, partner…) — not only villa staff.
    collectors = set(Payment.objects.filter(method=Payment.Method.CASH, is_cancelled=False, collected_by__is_owner=False, invoice__partition__villa__in=scope).values_list('collected_by', 'invoice__partition__villa'))
    collectors |= set(_cash_deposits().filter(collected_by__is_owner=False, tenant__partition__villa__in=scope).values_list('collected_by', 'tenant__partition__villa'))
    extra_villas = {v.pk: v for v in Villa.objects.filter(pk__in={vid for _, vid in collectors})}
    for user_id, villa_id in collectors:
        villas = villas_by_staff.setdefault(user_id, [])
        if villa_id not in {v.pk for v in villas}:
            villas.append(extra_villas[villa_id])
    staff_ids = set(villas_by_staff)
    if not staff_ids:
        return []
    collected = {r['collected_by']: r['total'] for r in Payment.objects.filter(collected_by__in=staff_ids, method=Payment.Method.CASH, is_cancelled=False, invoice__partition__villa__in=scope).order_by().values('collected_by').annotate(total=Sum('amount'))}
    for r in _cash_deposits().filter(collected_by__in=staff_ids, tenant__partition__villa__in=scope).order_by().values('collected_by').annotate(total=Sum('amount')):
        collected[r['collected_by']] = collected.get(r['collected_by'], Decimal('0.00')) + r['total']
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


def cash_with_owner(villas=None) -> dict:
    """Cash that is in the admin's hands: cash the admin collected directly, plus what the admin confirmed receiving
    from staff handovers. Staff-paid expenses are already netted off those handovers."""
    zero = Decimal('0.00')
    payments = Payment.objects.filter(method=Payment.Method.CASH, is_cancelled=False, collected_by__is_owner=True)
    deposits = _cash_deposits().filter(collected_by__is_owner=True)
    handovers = CashHandover.objects.filter(status=CashHandover.Status.CONFIRMED)
    if villas is not None:
        payments = payments.filter(invoice__partition__villa__in=villas)
        deposits = deposits.filter(tenant__partition__villa__in=villas)
        handovers = handovers.filter(Q(payments__invoice__partition__villa__in=villas) | Q(deposits__tenant__partition__villa__in=villas)).distinct()
    direct = (payments.aggregate(t=Sum('amount'))['t'] or zero) + (deposits.aggregate(t=Sum('amount'))['t'] or zero)
    from_staff = handovers.aggregate(t=Sum('confirmed_amount'))['t'] or zero
    return {'direct': direct, 'from_staff': from_staff, 'total': direct + from_staff}

def unhanded_staff_cash_for_month(villa, year: int, month: int) -> Decimal:
    """Cash staff collected that is income of this villa for one month (its rent, or advance collected in it) and
    that the admin has not yet confirmed receiving.

    Profit of a month can be shared once that month's cash is in admin hands — cash for other months does not block it.
    """
    from apps.billing.selectors import income_deposits, income_payments, period_bounds
    from apps.billing.models import Invoice
    start, end = period_bounds(year, month)
    rent = income_payments(Invoice.objects.filter(partition__villa=villa), start, end).filter(method=Payment.Method.CASH, collected_by__is_owner=False).exclude(handovers__status=CashHandover.Status.CONFIRMED).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    advance = income_deposits(_cash_deposits().filter(tenant__partition__villa=villa), start, end).filter(collected_by__is_owner=False).exclude(handovers__status=CashHandover.Status.CONFIRMED).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    return rent + advance

def villa_cash_position(villa) -> dict:
    """Where the cash collected in one villa is: still with staff, handed over, or spent on its expenses.

    holding = staff cash collected − expenses staff paid − what the admin confirmed receiving from staff handovers.
    Cash collected by an admin is already in admin hands; every other collector must hand it over
    (the same rule submit_cash_handover enforces).
    """
    zero = Decimal('0.00')
    in_villa = Payment.objects.filter(method=Payment.Method.CASH, is_cancelled=False, invoice__partition__villa=villa)
    deposits_in_villa = _cash_deposits().filter(tenant__partition__villa=villa)
    collected = (in_villa.aggregate(t=Sum('amount'))['t'] or zero) + (deposits_in_villa.aggregate(t=Sum('amount'))['t'] or zero)
    spent = Expense.objects.filter(villa=villa, status=Expense.Status.ACTIVE, paid_by=PaidBy.STAFF, paid_on__isnull=False).aggregate(t=Sum('amount'))['t'] or zero

    def handed(status):
        """This villa's part of the handovers in a status. A confirmed handover may be short (admin confirmed less
        than declared): the villa's part is scaled by confirmed/declared and the gap stays with the staff."""
        total, short = zero, zero
        handovers = CashHandover.objects.filter(status=status).filter(Q(payments__invoice__partition__villa=villa) | Q(deposits__tenant__partition__villa=villa) | Q(expenses__villa=villa)).distinct().prefetch_related('payments__invoice__partition', 'deposits__tenant__partition', 'expenses')
        for handover in handovers:
            part = (sum((p.amount for p in handover.payments.all() if p.invoice.partition.villa_id == villa.pk), zero)
                    + sum((d.amount for d in handover.deposits.all() if d.tenant.partition.villa_id == villa.pk), zero)
                    - sum((e.amount for e in handover.expenses.all() if e.villa_id == villa.pk), zero))
            if status == CashHandover.Status.CONFIRMED and handover.declared_amount and handover.confirmed_amount is not None and handover.confirmed_amount != handover.declared_amount:
                got = (part * handover.confirmed_amount / handover.declared_amount).quantize(Decimal('0.01'))
                short += part - got
                part = got
            total += part
        return total, short

    received, short = handed(CashHandover.Status.CONFIRMED)
    awaiting, _ = handed(CashHandover.Status.SUBMITTED)
    owner_direct = (in_villa.filter(collected_by__is_owner=True).aggregate(t=Sum('amount'))['t'] or zero) + (deposits_in_villa.filter(collected_by__is_owner=True).aggregate(t=Sum('amount'))['t'] or zero)
    received_by_owner = received + owner_direct
    staff_collected = collected - owner_direct
    holding = max(zero, staff_collected - spent - received)
    return {'collected': collected, 'spent_by_staff': spent, 'received_by_owner': received_by_owner, 'awaiting_confirmation': awaiting, 'holding': holding, 'short_in_handovers': max(zero, short)}

