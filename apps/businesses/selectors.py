from datetime import date
from decimal import Decimal
from django.db.models import Count, Q
from .models import PartnerPayout, PartnerVillaShare


def payouts_for_villas(villas, year: int, month: int | None = None, partner_ids=None) -> list:
    """Payouts that count against these villas' partner shares for a month (or a whole year when month is None).

    A payout recorded without a villa only counts when that partner has a share in a single villa; otherwise it is
    ambiguous and would wrongly show the partner as paid on every villa.
    """
    villa_ids = [getattr(v, 'pk', v) for v in villas]
    qs = PartnerPayout.objects.filter(Q(villa_id__in=villa_ids) | Q(villa__isnull=True), period_year=year)
    if month is not None:
        qs = qs.filter(period_month=month)
    if partner_ids is not None:
        qs = qs.filter(partner_id__in=list(partner_ids))
    payouts = list(qs.order_by('paid_on', 'created_at'))
    unassigned = {p.partner_id for p in payouts if p.villa_id is None}
    single_villa = {}
    if unassigned:
        counts = {row['partner_id']: row['n'] for row in PartnerVillaShare.objects.filter(partner_id__in=unassigned).values('partner_id').annotate(n=Count('pk'))}
        only_villa = dict(PartnerVillaShare.objects.filter(partner_id__in=[pid for pid, n in counts.items() if n == 1]).values_list('partner_id', 'villa_id'))
        single_villa = {pid: vid for pid, vid in only_villa.items() if vid in villa_ids}
    return [p for p in payouts if p.villa_id is not None or p.partner_id in single_villa]


def villa_period_payouts(villa, year: int, month: int | None) -> list:
    return payouts_for_villas([villa], year, month)


def profit_split(*, invoices, expenses, deposits, start: date, end: date, shares) -> dict:
    """Who gets the profit — or carries the loss — of a period, per villa and in total. Every page uses this.

    Everything goes by the shares: each villa's net (profit or loss) is split by its partners' percentages, and the
    owner keeps the remaining percentage. A partner's share of a loss is negative. Partners only share in the
    villas they hold shares in, so a loss elsewhere never touches them. Owner + partners always equals the net.
    """
    from collections import defaultdict
    from django.db.models import Sum
    from apps.billing import selectors as billing_selectors
    from apps.expenses.models import Expense
    zero = Decimal('0.00')
    nets = defaultdict(lambda: zero)  # villa_id -> net of the period
    for row in billing_selectors.income_payments(invoices, start, end).order_by().values('invoice__partition__villa_id').annotate(t=Sum('amount')):
        nets[row['invoice__partition__villa_id']] += row['t']
    for row in billing_selectors.income_deposits(deposits, start, end).order_by().values('tenant__partition__villa_id').annotate(t=Sum('amount')):
        nets[row['tenant__partition__villa_id']] += row['t']
    for row in expenses.filter(status=Expense.Status.ACTIVE, date__gte=start, date__lte=end).order_by().values('villa_id').annotate(t=Sum('amount')):
        nets[row['villa_id']] -= row['t']
    shares_by_villa = defaultdict(list)
    for share in shares:
        shares_by_villa[share.villa_id].append(share)
    villas, by_share, by_partner = {}, defaultdict(lambda: zero), defaultdict(lambda: zero)
    for villa_id, net in nets.items():
        v = villas.setdefault(villa_id, {'net': net, 'partner_cut': zero, 'owner_share': zero})
        for share in shares_by_villa.get(villa_id, []):
            cut = (net * share.percentage / Decimal('100.00')).quantize(Decimal('0.01'))
            by_share[share.pk] += cut
            by_partner[share.partner_id] += cut
            v['partner_cut'] += cut
        v['owner_share'] = v['net'] - v['partner_cut']
    net = sum((v['net'] for v in villas.values()), zero)
    partner_cut = sum((v['partner_cut'] for v in villas.values()), zero)
    return {'villas': villas, 'net': net, 'partner_cut': partner_cut, 'owner_share': net - partner_cut, 'by_share': dict(by_share), 'by_partner': dict(by_partner)}


def partner_balance(partner, *, invoices, expenses, deposits, today=None) -> dict:
    """A partner's account across all time: their share of every profit and loss so far, minus what was paid.

    Positive balance = still owed to the partner; negative = the partner's loss shares exceed what they were paid
    (it is offset against their future profit shares).
    """
    from django.db.models import Sum
    today = today or date.today()
    shares = list(partner.villa_shares.all())
    split = profit_split(invoices=invoices.filter(partition__villa_id__in=[s.villa_id for s in shares]), expenses=expenses, deposits=deposits, start=date(2000, 1, 1), end=today, shares=shares)
    earned = sum((split['by_share'].get(s.pk, Decimal('0.00')) for s in shares), Decimal('0.00'))
    paid = partner.payouts.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    return {'earned': earned, 'paid': paid, 'balance': earned - paid}


EMPTY_VILLA_SPLIT ={'net': Decimal('0.00'), 'partner_cut': Decimal('0.00'), 'owner_share': Decimal('0.00')}


def partner_shares_due_after_payout(today=None) -> list[dict]:
    """Past months whose partners were already (partly) paid, but whose profit has since grown — e.g. arrears for
    that month were collected later. Every month is isolated, so that extra profit belongs to that month's partners."""
    from apps.billing import selectors as billing_selectors
    from apps.billing.models import Invoice
    from apps.expenses.models import Expense
    from apps.tenancy.models import DepositCollection
    from apps.villas.models import Villa
    this_month = (today or date.today()).replace(day=1)
    keys = PartnerPayout.objects.filter(villa__isnull=False).values_list('villa_id', 'period_year', 'period_month').distinct()
    due = []
    for villa_id, year, month in keys:
        if date(year, month, 1) >= this_month:
            continue
        villa = Villa.objects.get(pk=villa_id)
        start, end = billing_selectors.period_bounds(year, month)
        net = billing_selectors.period_profit_loss(Invoice.objects.filter(partition__villa=villa), Expense.objects.filter(villa=villa), start, end, deposits=DepositCollection.objects.filter(tenant__partition__villa=villa))['net']
        payouts = villa_period_payouts(villa, year, month)
        remaining = Decimal('0.00')
        for share in PartnerVillaShare.objects.filter(villa=villa, partner__is_active=True):
            cut = (net * (share.percentage / Decimal('100.00'))).quantize(Decimal('0.01'))
            paid = sum((p.amount for p in payouts if p.partner_id == share.partner_id), Decimal('0.00'))
            remaining += max(Decimal('0.00'), cut - paid)
        if remaining > 0:
            due.append({'villa': villa, 'year': year, 'month': month, 'remaining': remaining})
    return due
