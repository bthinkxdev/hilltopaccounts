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
    """Who gets the profit of a period, per villa and in total — the one calculation every page uses.

    Partners are paid month by month on that month's profit; a loss month pays them nothing and is not carried
    over (that is how payouts are recorded). So a period's partner cut = Σ over its months of max(0, month net) × %.
    The owner keeps the rest and bears every loss: owner share = net − partner cuts (negative when there is a loss),
    so owner + partners always equals the net profit exactly.
    """
    from collections import defaultdict
    from django.db.models import Sum
    from django.db.models.functions import TruncMonth
    from apps.billing import selectors as billing_selectors
    from apps.expenses.models import Expense
    zero = Decimal('0.00')
    monthly = defaultdict(lambda: zero)  # (villa_id, month) -> net
    for row in billing_selectors.income_payments(invoices, start, end).annotate(m=TruncMonth('invoice__billing_period_start')).values('invoice__partition__villa_id', 'm').annotate(t=Sum('amount')):
        monthly[(row['invoice__partition__villa_id'], row['m'])] += row['t']
    for row in billing_selectors.income_deposits(deposits, start, end).annotate(m=TruncMonth('collected_at')).values('tenant__partition__villa_id', 'm').annotate(t=Sum('amount')):
        monthly[(row['tenant__partition__villa_id'], row['m'])] += row['t']
    for row in expenses.filter(status=Expense.Status.ACTIVE, date__gte=start, date__lte=end).annotate(m=TruncMonth('date')).values('villa_id', 'm').annotate(t=Sum('amount')):
        monthly[(row['villa_id'], row['m'])] -= row['t']
    shares_by_villa = defaultdict(list)
    for share in shares:
        shares_by_villa[share.villa_id].append(share)
    villas, by_share, by_partner = {}, defaultdict(lambda: zero), defaultdict(lambda: zero)
    for (villa_id, _month), net in monthly.items():
        v = villas.setdefault(villa_id, {'net': zero, 'partner_cut': zero, 'owner_share': zero})
        v['net'] += net
        if net > 0:
            for share in shares_by_villa.get(villa_id, []):
                cut = (net * share.percentage / Decimal('100.00')).quantize(Decimal('0.01'))
                by_share[share.pk] += cut
                by_partner[share.partner_id] += cut
                v['partner_cut'] += cut
    for v in villas.values():
        v['owner_share'] = v['net'] - v['partner_cut']
    net = sum((v['net'] for v in villas.values()), zero)
    partner_cut = sum((v['partner_cut'] for v in villas.values()), zero)
    return {'villas': villas, 'net': net, 'partner_cut': partner_cut, 'owner_share': net - partner_cut, 'by_share': dict(by_share), 'by_partner': dict(by_partner)}


EMPTY_VILLA_SPLIT = {'net': Decimal('0.00'), 'partner_cut': Decimal('0.00'), 'owner_share': Decimal('0.00')}


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
        distributable = max(Decimal('0.00'), net)
        payouts = villa_period_payouts(villa, year, month)
        remaining = Decimal('0.00')
        for share in PartnerVillaShare.objects.filter(villa=villa, partner__is_active=True):
            cut = (distributable * (share.percentage / Decimal('100.00'))).quantize(Decimal('0.01'))
            paid = sum((p.amount for p in payouts if p.partner_id == share.partner_id), Decimal('0.00'))
            remaining += max(Decimal('0.00'), cut - paid)
        if remaining > 0:
            due.append({'villa': villa, 'year': year, 'month': month, 'remaining': remaining})
    return due
