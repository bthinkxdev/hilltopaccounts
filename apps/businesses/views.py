from datetime import date
from decimal import Decimal
import calendar
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from apps.accounts import selectors
from apps.billing import selectors as billing_selectors
from apps.expenses import selectors as expenses_selectors
from apps.shared.exceptions import DomainError
from apps.shared.pagination import paginate_queryset
from apps.shared.periods import period_context
from apps.audit import services as audit_services
from apps.audit.models import Action
from apps.villas.models import Villa
from .forms import ArchiveReasonForm, BusinessForm, PartnerForm, PartnerPayoutForm, PartnerShareForm
from .models import Partner, PartnerPayout, PartnerVillaShare
from .services import archive_business, create_business, unarchive_business

@login_required
def business_list(request):
    status = request.GET.get('status', 'active')
    if status == 'archived':
        qs = selectors.businesses_visible_to(request.user).filter(is_archived=True)
    elif status == 'all':
        qs = selectors.businesses_visible_to(request.user)
    else:
        qs = selectors.businesses_visible_to(request.user).filter(is_archived=False)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(name__icontains=q)
    page_obj = paginate_queryset(request, qs)
    for business in page_obj:
        business.scoped_villa_count = selectors.villas_visible_to(request.user).filter(business=business).count()
    return render(request, 'businesses/list.html', {'page_obj': page_obj, 'q': q, 'selected_status': status, 'breadcrumbs': [('Businesses', None)]})

@login_required
def business_detail(request, pk):
    business = get_object_or_404(selectors.businesses_visible_to(request.user), pk=pk)
    villas = selectors.villas_visible_to(request.user).filter(business=business, is_archived=False)
    period = period_context(request)
    financial = selectors.financial_villas(request.user).filter(business=business)
    context = {'period_pnl': billing_selectors.period_profit_loss(selectors.invoices_visible_to(request.user).filter(partition__villa__in=financial), selectors.expenses_visible_to(request.user).filter(villa__in=financial), period['period_start'], period['period_end']) if financial.exists() else None, 'keep': [], **period, 'business': business, 'villas': villas, 'can_view_financials': selectors.can_view_financial_kpis(request.user), 'breadcrumbs': [('Businesses', reverse('businesses:list')), (business.name, None)]}
    if context['can_view_financials']:
        invoices = selectors.invoices_visible_to(request.user).filter(partition__villa__business=business)
        expenses = selectors.expenses_visible_to(request.user).filter(business=business)
        context.update(expected_revenue=billing_selectors.expected_revenue(invoices), collected=billing_selectors.collected_amount(invoices), outstanding=billing_selectors.outstanding_amount(invoices), total_expenses=expenses_selectors.valid_expense_total(expenses), net_profit=billing_selectors.profit(invoices, expenses))
    return render(request, 'businesses/detail.html', context)

@login_required
def business_create(request):
    if not request.user.is_owner:
        raise PermissionDenied('Only the Owner can create a business.')
    if request.method == 'POST':
        form = BusinessForm(request.POST)
        if form.is_valid():
            business = create_business(name=form.cleaned_data['name'], description=form.cleaned_data['description'], created_by=request.user)
            messages.success(request, f'Business “{business.name}” created.')
            return redirect('businesses:detail', pk=business.pk)
    else:
        form = BusinessForm()
    return render(request, 'components/form_page.html', {'form': form, 'title': 'Add Business', 'cancel_url': reverse('businesses:list'), 'breadcrumbs': [('Businesses', reverse('businesses:list')), ('New', None)]})

@login_required
def business_archive(request, pk):
    business = get_object_or_404(selectors.businesses_visible_to(request.user), pk=pk)
    if not request.user.is_owner:
        raise PermissionDenied('Only the Owner can archive a business.')
    if request.method == 'POST':
        form = ArchiveReasonForm(request.POST)
        if form.is_valid():
            try:
                archive_business(business=business, archived_by=request.user, reason=form.cleaned_data['reason'])
            except DomainError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f'Business “{business.name}” archived.')
                return redirect('businesses:list')
    else:
        form = ArchiveReasonForm()
    return render(request, 'components/confirm_reason.html', {'form': form, 'title': f'Archive {business.name}?', 'message': 'This business will be archived, not deleted — its history stays intact and visible.', 'cancel_url': reverse('businesses:detail', args=[business.pk]), 'breadcrumbs': [('Businesses', reverse('businesses:list')), (business.name, reverse('businesses:detail', args=[business.pk])), ('Archive', None)]})


@login_required
def business_unarchive(request, pk):
    business = get_object_or_404(selectors.businesses_visible_to(request.user), pk=pk)
    if not request.user.is_owner:
        raise PermissionDenied('Only the Owner can unarchive a business.')
    if request.method == 'POST':
        unarchive_business(business=business, unarchived_by=request.user)
        messages.success(request, f'Business “{business.name}” unarchived.')
    return redirect('businesses:detail', pk=business.pk)


@login_required
def partner_list(request):
    if not request.user.is_owner:
        raise PermissionDenied('Only the owner can view partners.')
    status, q = request.GET.get('status', 'active'), request.GET.get('q', '').strip()
    qs = Partner.objects.all().prefetch_related('villa_shares__villa')
    if status in ('active', 'inactive'):
        qs = qs.filter(is_active=(status == 'active'))
    if q:
        qs = qs.filter(name__icontains=q)

    period = period_context(request)
    villas = selectors.villas_visible_to(request.user).filter(is_archived=False)
    pnl = billing_selectors.profit_loss_by_villa(
        selectors.invoices_visible_to(request.user).filter(partition__villa__in=villas),
        selectors.expenses_visible_to(request.user).filter(villa__in=villas),
        period['period_start'], period['period_end']
    )
    page_obj = paginate_queryset(request, qs)
    partner_ids = [p.pk for p in page_obj]
    paid_by_partner = {
        item['partner_id']: (item['total'] or Decimal('0.00'))
        for item in PartnerPayout.objects.filter(
            partner_id__in=partner_ids,
            period_year=period['year'],
            period_month=period['month'],
        ).values('partner_id').annotate(total=models.Sum('amount'))
    }
    for p in page_obj:
        p.shares = list(p.villa_shares.all())
        p.shares_label = [f"{s.villa.name} ({s.percentage}%)" for s in p.shares]
        p.total_payout = sum(
            (max(Decimal('0.00'), pnl.get(s.villa_id, {}).get('net', Decimal('0.00'))) * (s.percentage / Decimal('100.00'))).quantize(Decimal('0.01'))
            for s in p.shares
        ) or Decimal('0.00')
        p.paid_amount = paid_by_partner.get(p.pk, Decimal('0.00'))
        if p.total_payout > 0:
            if p.paid_amount >= p.total_payout:
                p.payment_status = 'paid'
            elif p.paid_amount > 0:
                p.payment_status = 'partial'
            else:
                p.payment_status = 'pending'
        else:
            p.payment_status = 'none'

    return render(request, 'businesses/partner_list.html', {
        'page_obj': page_obj, 'q': q, 'selected_status': status,
        'breadcrumbs': [('Partners', None)],
        'keep': [(k, v) for k, v in (('q', q), ('status', status if status != 'active' else '')) if v],
        **period,
    })


@login_required
def partner_form(request, pk=None):
    if not request.user.is_owner:
        raise PermissionDenied('Only the owner can manage partners.')
    partner = get_object_or_404(Partner, pk=pk) if pk else None
    if request.method == 'POST':
        form = PartnerForm(request.POST, instance=partner)
        if form.is_valid():
            is_new = not pk
            old_val = {'name': partner.name, 'phone': partner.phone, 'email': partner.email, 'is_active': partner.is_active} if partner else None
            p = form.save(commit=False)
            if not pk:
                p.created_by = request.user
            p.save()
            new_val = {'name': p.name, 'phone': p.phone, 'email': p.email, 'is_active': p.is_active}
            audit_services.log(
                user=request.user,
                action=Action.PARTNER_CREATED if is_new else Action.PARTNER_UPDATED,
                obj=p,
                old_value=old_val,
                new_value=new_val,
                reason=f'Partner {"created" if is_new else "updated"}'
            )
            messages.success(request, f'Partner “{p.name}” {"updated" if pk else "created"}.')
            return redirect('businesses:partner_detail', pk=p.pk)
    else:
        form = PartnerForm(instance=partner)
    return render(request, 'components/form_page.html', {
        'form': form,
        'title': f'{"Edit" if pk else "Add"} Partner',
        'cancel_url': reverse('businesses:partner_detail', args=[pk]) if pk else reverse('businesses:partner_list'),
        'breadcrumbs': [('Partners', reverse('businesses:partner_list')), ((partner.name if partner else 'New'), None)],
    })

partner_create = partner_form
partner_edit = partner_form


@login_required
def partner_detail(request, pk):
    if not request.user.is_owner:
        raise PermissionDenied('Only the owner can view partner details.')
    partner = get_object_or_404(Partner, pk=pk)
    period = period_context(request)
    villas = selectors.villas_visible_to(request.user).filter(is_archived=False)
    pnl = billing_selectors.profit_loss_by_villa(
        selectors.invoices_visible_to(request.user).filter(partition__villa__in=villas),
        selectors.expenses_visible_to(request.user).filter(villa__in=villas),
        period['period_start'], period['period_end']
    )
    shares = list(partner.villa_shares.select_related('villa').order_by('villa__name'))
    villa_ids = [s.villa_id for s in shares]
    all_shares_for_villas = list(
        PartnerVillaShare.objects.filter(
            villa_id__in=villa_ids, partner__is_active=True
        ).select_related('partner')
    )
    payout_history = list(partner.payouts.select_related('villa').order_by('-paid_on', '-created_at'))
    period_payouts = [p for p in payout_history if p.period_year == period['year'] and p.period_month == period['month']]
    period_paid_amount = sum((p.amount for p in period_payouts), Decimal('0.00'))
    existing_payouts_for_period = {p.villa_id: p for p in period_payouts if p.villa_id}
    unassigned_payout = next((p for p in period_payouts if not p.villa_id), None)

    for s in shares:
        v = pnl.get(s.villa_id, billing_selectors.EMPTY_PROFIT_LOSS)
        s.villa_income, s.villa_expenses, s.villa_net = v['income'], v['expenses'], v['net']
        distributable_net = max(Decimal('0.00'), s.villa_net)
        s.cut = (distributable_net * (s.percentage / Decimal('100.00'))).quantize(Decimal('0.01'))
        s.co_partners = [ps for ps in all_shares_for_villas if ps.villa_id == s.villa_id and ps.partner_id != partner.pk]
        for cp in s.co_partners:
            cp.cut = (distributable_net * (cp.percentage / Decimal('100.00'))).quantize(Decimal('0.01'))
        total_villa_partner_pct = sum((ps.percentage for ps in all_shares_for_villas if ps.villa_id == s.villa_id), Decimal('0.00'))
        s.owner_percentage = max(Decimal('0.00'), Decimal('100.00') - total_villa_partner_pct)
        s.owner_cut = (distributable_net * (s.owner_percentage / Decimal('100.00'))).quantize(Decimal('0.01'))
        s.villa_payouts = [p for p in period_payouts if p.villa_id == s.villa_id]
        if not s.villa_payouts and (len(shares) == 1 or unassigned_payout):
            s.villa_payouts = period_payouts
        s.paid_amount = sum((p.amount for p in s.villa_payouts), Decimal('0.00'))
        s.remaining = max(Decimal('0.00'), s.cut - s.paid_amount)
        s.is_fully_paid = (s.paid_amount >= s.cut and s.cut > 0)
        s.is_partial = (0 < s.paid_amount < s.cut)
        s.payout = s.villa_payouts[-1] if s.villa_payouts else None

    total_payout = sum((s.cut for s in shares), Decimal('0.00'))
    remaining_payout = max(Decimal('0.00'), total_payout - period_paid_amount)

    return render(request, 'businesses/partner_detail.html', {
        'partner': partner, 'shares': shares,
        'total_payout': total_payout,
        'period_paid_amount': period_paid_amount,
        'remaining_payout': remaining_payout,
        'is_fully_paid': (period_paid_amount >= total_payout and total_payout > 0),
        'period_payouts': period_payouts,
        'payout_history': payout_history,
        'breadcrumbs': [('Partners', reverse('businesses:partner_list')), (partner.name, None)],
        'keep': [], **period,
    })


@login_required
def partner_share_create(request, partner_pk):
    if not request.user.is_owner:
        raise PermissionDenied('Only the owner can assign shares.')
    partner = get_object_or_404(Partner, pk=partner_pk)
    if request.method == 'POST':
        form = PartnerShareForm(request.POST, partner=partner)
        if form.is_valid():
            share = form.save(commit=False)
            share.partner = partner
            share.save()
            audit_services.log(
                user=request.user,
                action=Action.PARTNER_SHARE_CREATED,
                obj=share,
                villa=share.villa,
                business=share.villa.business,
                new_value={'percentage': str(share.percentage), 'partner': partner.name, 'villa': share.villa.name},
                reason=f'Assigned {share.percentage}% share in {share.villa.name} to {partner.name}'
            )
            messages.success(request, f'Share for “{share.villa.name}” added to {partner.name}.')
            return redirect('businesses:partner_detail', pk=partner.pk)
    else:
        form = PartnerShareForm(partner=partner)
    return render(request, 'components/form_page.html', {
        'form': form,
        'title': f'Assign Villa Share — {partner.name}',
        'submit_label': 'Assign Share',
        'cancel_url': reverse('businesses:partner_detail', args=[partner.pk]),
        'breadcrumbs': [('Partners', reverse('businesses:partner_list')), (partner.name, reverse('businesses:partner_detail', args=[partner.pk])), ('Add Share', None)],
    })


@login_required
@require_POST
def partner_share_edit(request, pk):
    if not request.user.is_owner:
        raise PermissionDenied('Only the owner can edit shares.')
    share = get_object_or_404(PartnerVillaShare, pk=pk)
    try:
        old_pct = str(share.percentage)
        raw_pct = request.POST.get('percentage', '').strip()
        share.percentage = Decimal(raw_pct)
        share.full_clean()
        share.save()
        audit_services.log(
            user=request.user,
            action=Action.PARTNER_SHARE_UPDATED,
            obj=share,
            villa=share.villa,
            business=share.villa.business,
            old_value={'percentage': old_pct},
            new_value={'percentage': str(share.percentage)},
            reason=f'Updated {share.partner.name}’s share in {share.villa.name} from {old_pct}% to {share.percentage}%'
        )
        messages.success(request, f'Updated {share.partner.name}’s share for {share.villa.name} to {share.percentage}%.')
    except ValidationError as e:
        msg = e.message_dict.get('percentage', [str(e)])[0] if hasattr(e, 'message_dict') else str(e)
        messages.error(request, f'Could not update share: {msg}')
    except Exception as e:
        messages.error(request, f'Could not update share: {e}')
    return redirect('businesses:partner_detail', pk=share.partner_id)


@login_required
@require_POST
def partner_share_delete(request, pk):
    if not request.user.is_owner:
        raise PermissionDenied('Only the owner can remove shares.')
    share = get_object_or_404(PartnerVillaShare, pk=pk)
    partner_pk, villa_name = share.partner_id, share.villa.name
    villa_obj, partner_obj, pct = share.villa, share.partner, str(share.percentage)
    share.delete()
    audit_services.log(
        user=request.user,
        action=Action.PARTNER_SHARE_DELETED,
        villa=villa_obj,
        business=villa_obj.business,
        old_value={'partner': partner_obj.name, 'villa': villa_name, 'percentage': pct},
        reason=f'Removed {partner_obj.name}’s {pct}% share from {villa_name}'
    )
    messages.success(request, f'Removed share for {villa_name}.')
    return redirect('businesses:partner_detail', pk=partner_pk)


@login_required
def partner_payout_create(request, partner_pk):
    if not request.user.is_owner:
        raise PermissionDenied('Only the owner can record payouts.')
    partner = get_object_or_404(Partner, pk=partner_pk)
    villa_id = request.GET.get('villa') or request.POST.get('villa')
    villa = get_object_or_404(Villa, pk=villa_id) if villa_id else None
    if not villa and partner.villa_shares.count() == 1:
        villa = partner.villa_shares.first().villa
    year = int(request.GET.get('year') or request.POST.get('year') or date.today().year)
    month = int(request.GET.get('month') or request.POST.get('month') or date.today().month)
    initial_amount = request.GET.get('amount')
    if initial_amount:
        try:
            if Decimal(initial_amount) <= 0:
                initial_amount = None
        except Exception:
            initial_amount = None

    if request.method == 'POST':
        form = PartnerPayoutForm(request.POST)
        if form.is_valid():
            payout = form.save(commit=False)
            payout.partner = partner
            payout.villa = villa
            payout.period_year = year
            payout.period_month = month
            payout.created_by = request.user
            payout.save()
            audit_services.log(
                user=request.user,
                action=Action.PARTNER_PAYOUT_CREATED,
                obj=payout,
                villa=payout.villa,
                business=payout.villa.business if payout.villa else None,
                new_value={
                    'partner': partner.name,
                    'amount': str(payout.amount),
                    'method': payout.payment_method,
                    'paid_on': str(payout.paid_on),
                    'period': f'{payout.period_year}-{payout.period_month:02d}',
                },
                reason=f'Paid QAR {payout.amount} to {partner.name} for {payout.period_year}-{payout.period_month:02d} via {payout.get_payment_method_display()}'
            )
            messages.success(request, f'Recorded payout of QAR {payout.amount} to {partner.name}.')
            if villa and (request.GET.get('from') == 'villa' or request.POST.get('from') == 'villa'):
                return redirect(f"{reverse('villas:villa_detail', args=[villa.pk])}?period=month&year={year}&month={month}")
            return redirect(f"{reverse('businesses:partner_detail', args=[partner.pk])}?period=month&year={year}&month={month}")
    else:
        form = PartnerPayoutForm(initial={'amount': initial_amount})
    return render(request, 'components/form_page.html', {
        'form': form,
        'title': f'Record Profit Payout — {partner.name} ({year}-{month:02d})',
        'submit_label': 'Record Payout',
        'cancel_url': (
            f"{reverse('villas:villa_detail', args=[villa.pk])}?period=month&year={year}&month={month}"
            if (villa and request.GET.get('from') == 'villa')
            else f"{reverse('businesses:partner_detail', args=[partner.pk])}?period=month&year={year}&month={month}"
        ),
        'breadcrumbs': [('Partners', reverse('businesses:partner_list')), (partner.name, reverse('businesses:partner_detail', args=[partner.pk])), ('Record Payout', None)],
    })


@login_required
@require_POST
def partner_payout_delete(request, pk):
    if not request.user.is_owner:
        raise PermissionDenied('Only the owner can delete payouts.')
    payout = get_object_or_404(PartnerPayout, pk=pk)
    partner_pk = payout.partner_id
    amt, partner_name, yr, mo = str(payout.amount), payout.partner.name, payout.period_year, payout.period_month
    v_obj = payout.villa
    payout.delete()
    audit_services.log(
        user=request.user,
        action=Action.PARTNER_PAYOUT_DELETED,
        villa=v_obj,
        business=v_obj.business if v_obj else None,
        old_value={'partner': partner_name, 'amount': amt, 'period': f'{yr}-{mo:02d}'},
        reason=f'Deleted payout record of QAR {amt} to {partner_name} for {yr}-{mo:02d}'
    )
    messages.success(request, 'Payout record deleted.')
    return redirect('businesses:partner_detail', pk=partner_pk)


@login_required
def villa_partner_payout_batch(request, villa_pk):
    if not request.user.is_owner:
        raise PermissionDenied('Only the owner can record partner payouts.')
    villa = get_object_or_404(selectors.villas_visible_to(request.user), pk=villa_pk)
    today = date.today()
    year = int(request.GET.get('year') or request.POST.get('year') or today.year)
    month = int(request.GET.get('month') or request.POST.get('month') or today.month)

    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    invoices = selectors.invoices_visible_to(request.user).filter(partition__villa=villa)
    expenses = selectors.expenses_visible_to(request.user).filter(villa=villa)
    pnl = billing_selectors.period_profit_loss(invoices, expenses, start, end)
    net_profit = pnl.get('net', Decimal('0.00'))
    distributable_profit = max(Decimal('0.00'), net_profit)

    shares = list(PartnerVillaShare.objects.filter(villa=villa, partner__is_active=True).select_related('partner'))
    payouts_qs = list(PartnerPayout.objects.filter(
        models.Q(villa=villa) | models.Q(villa__isnull=True),
        period_year=year, period_month=month
    ).order_by('paid_on', 'created_at'))

    for s in shares:
        s.cut = (distributable_profit * (s.percentage / Decimal('100.00'))).quantize(Decimal('0.01'))
        partner_payouts = [p for p in payouts_qs if p.partner_id == s.partner_id]
        s.paid_amount = sum((p.amount for p in partner_payouts), Decimal('0.00'))
        s.remaining = max(Decimal('0.00'), s.cut - s.paid_amount)
        s.is_fully_paid = (s.paid_amount >= s.cut and s.cut > 0)
        s.is_partial = (0 < s.paid_amount < s.cut)
        s.existing_payout = partner_payouts[-1] if partner_payouts else None

    unpaid_shares = [s for s in shares if s.remaining > 0]
    total_unpaid = sum((s.remaining for s in unpaid_shares), Decimal('0.00'))

    if request.method == 'POST':
        if distributable_profit <= 0:
            messages.error(request, 'Cannot record partner payouts when villa net profit is zero or negative.')
            return redirect(f"{reverse('villas:villa_detail', args=[villa.pk])}?period=month&year={year}&month={month}")
        if not unpaid_shares:
            messages.warning(request, 'No pending partner payouts to record.')
            return redirect(f"{reverse('villas:villa_detail', args=[villa.pk])}?period=month&year={year}&month={month}")
        method = request.POST.get('payment_method', 'bank_transfer')
        paid_on = request.POST.get('paid_on') or today.isoformat()
        reference = request.POST.get('reference', '').strip()
        notes = request.POST.get('notes', '').strip()
        created_count = 0
        for s in unpaid_shares:
            p = PartnerPayout.objects.create(
                partner=s.partner,
                villa=villa,
                period_year=year,
                period_month=month,
                amount=s.remaining,
                payment_method=method,
                paid_on=paid_on,
                reference=reference,
                notes=notes,
                created_by=request.user,
            )
            audit_services.log(
                user=request.user,
                action=Action.PARTNER_PAYOUT_CREATED,
                obj=p,
                villa=villa,
                business=villa.business,
                new_value={
                    'partner': s.partner.name,
                    'amount': str(s.remaining),
                    'method': method,
                    'paid_on': str(paid_on),
                    'period': f'{year}-{month:02d}',
                },
                reason=f'Batch payout QAR {s.remaining} to {s.partner.name} for {villa.name} ({year}-{month:02d}) via {p.get_payment_method_display()}'
            )
            created_count += 1
        messages.success(request, f'Recorded profit payout for {created_count} partner(s) totalling QAR {total_unpaid}.')
        return redirect(f"{reverse('villas:villa_detail', args=[villa.pk])}?period=month&year={year}&month={month}")

    return render(request, 'businesses/villa_payout_batch.html', {
        'villa': villa,
        'year': year,
        'month': month,
        'month_label': f'{calendar.month_name[month]} {year}',
        'net_profit': net_profit,
        'shares': shares,
        'unpaid_shares': unpaid_shares,
        'total_unpaid': total_unpaid,
        'today': today.isoformat(),
        'breadcrumbs': [('Villas', reverse('villas:villa_list')), (villa.name, reverse('villas:villa_detail', args=[villa.pk])), ('Record Payouts', None)],
    })

