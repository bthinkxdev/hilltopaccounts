import csv
from datetime import date, datetime
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.db.models import Q, Sum
from django.utils.dateparse import parse_date
from django.shortcuts import render
from django.urls import reverse
from django.views.generic import TemplateView
from apps.accounts import selectors
from apps.billing import selectors as billing_selectors
from apps.cash import selectors as cash_selectors
from apps.shared.periods import period_context
from apps.cash.models import CashHandover
from apps.expenses import selectors as expenses_selectors
from apps.tenancy.models import Tenant

class HomeView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/home.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        businesses = selectors.businesses_visible_to(user).filter(is_archived=False)
        villas = selectors.villas_visible_to(user).filter(is_archived=False, business__is_archived=False, is_sold=False).select_related('business')
        partitions = selectors.partitions_visible_to(user).filter(status='active', villa__is_archived=False, villa__business__is_archived=False, villa__is_sold=False)
        staff_view = selectors.is_field_staff(user)
        ctx['staff_view'] = staff_view
        ctx['businesses'] = businesses
        ctx['villas'] = villas
        ctx['role_labels'] = selectors.role_labels_for(user)
        ctx['business_count'] = businesses.count()
        ctx['villa_count'] = villas.count()
        partition_count = partitions.count()
        occupied_count = partitions.filter(tenancies__status=Tenant.Status.ACTIVE).distinct().count()
        ctx['partition_count'] = partition_count
        ctx['occupied_count'] = occupied_count
        ctx['vacant_count'] = partition_count - occupied_count
        ctx.update(period_context(self.request))
        ctx['keep'] = []
        financial = selectors.financial_villas(user)
        if not financial.exists() and staff_view:
            financial = selectors.villas_visible_to(user)
        ctx['period_pnl'] = billing_selectors.period_profit_loss(selectors.invoices_visible_to(user).filter(partition__villa__in=financial), selectors.expenses_visible_to(user).filter(villa__in=financial), ctx['period_start'], ctx['period_end']) if financial.exists() else None
        if ctx['period_pnl']:
            ctx['pnl'] = ctx['period_pnl']
            ctx['period_pnl_urls'] = {
                'income': f"{reverse('billing:payment_list')}?tab=collected&year={ctx['year']}&month={ctx['month']}",
                'expenses': f"{reverse('expenses:list')}?year={ctx['year']}&month={ctx['month']}",
                'profit': f"{reverse('businesses:partner_list')}?tab=all&year={ctx['year']}&month={ctx['month']}" if user.is_owner else '#',
                'owner_profit': f"{reverse('businesses:partner_list')}?tab=owner&year={ctx['year']}&month={ctx['month']}" if user.is_owner else '#',
                'profit_share': f"{reverse('businesses:partner_list')}?tab=partners&year={ctx['year']}&month={ctx['month']}" if user.is_owner else '#',
                'staff_collections': f"{reverse('billing:payment_list')}?tab=collected&collector=staff&year={ctx['year']}&month={ctx['month']}",
                'admin_collections': f"{reverse('billing:payment_list')}?tab=collected&collector=admin&year={ctx['year']}&month={ctx['month']}",
                'staff_expenses': f"{reverse('expenses:list')}?paid_by=staff&year={ctx['year']}&month={ctx['month']}",
                'admin_expenses': f"{reverse('expenses:list')}?paid_by=owner&year={ctx['year']}&month={ctx['month']}",
            }
            net_profit = ctx['period_pnl'].get('net', Decimal('0.00'))
            distributable = max(Decimal('0.00'), net_profit)
            from apps.businesses.models import PartnerVillaShare
            active_shares = list(PartnerVillaShare.objects.filter(villa__in=financial, partner__is_active=True).select_related('partner'))
            partner_share_cards = []
            if active_shares and distributable > 0:
                pnl_by_v = billing_selectors.profit_loss_by_villa(
                    selectors.invoices_visible_to(user).filter(partition__villa__in=financial),
                    selectors.expenses_visible_to(user).filter(villa__in=financial),
                    ctx['period_start'], ctx['period_end']
                )
                tot_partner_cut = Decimal('0.00')
                partner_map = {}
                for s in active_shares:
                    v_net = pnl_by_v.get(s.villa_id, {}).get('net', Decimal('0.00'))
                    if v_net > 0:
                        c = (v_net * (s.percentage / Decimal('100.00'))).quantize(Decimal('0.01'))
                        tot_partner_cut += c
                        if s.partner_id not in partner_map:
                            partner_map[s.partner_id] = {
                                'name': s.partner.name,
                                'cut': c,
                                'url': (reverse('businesses:partner_detail', args=[s.partner.pk]) + f"?year={ctx['year']}&month={ctx['month']}") if user.is_owner else '#'
                            }
                        else:
                            partner_map[s.partner_id]['cut'] += c
                for p_data in partner_map.values():
                    p_data['percentage'] = ((p_data['cut'] / distributable) * Decimal('100.0')).quantize(Decimal('0.1')) if distributable > 0 else Decimal('0.0')
                partner_share_cards = list(partner_map.values())
                owner_cut = max(Decimal('0.00'), distributable - tot_partner_cut)
                owner_pct = ((owner_cut / distributable) * Decimal('100.0')).quantize(Decimal('0.1')) if distributable > 0 else Decimal('100.0')
                partner_count = len(partner_map)
            else:
                tot_partner_cut = Decimal('0.00')
                owner_cut = distributable
                owner_pct = Decimal('100.00')
                partner_share_cards = []
                partner_count = 0

            ctx['owner_share_amount'] = owner_cut
            ctx['owner_share_pct'] = owner_pct
            ctx['partner_share_cards'] = partner_share_cards
            ctx['total_partner_cut'] = tot_partner_cut
            ctx['partner_count'] = partner_count
            ctx['distributable_profit'] = distributable

        needs_attention = []
        quick_actions = []
        ctx['can_view_financials'] = selectors.can_view_financial_kpis(user)
        if ctx['can_view_financials']:
            invoices = selectors.invoices_visible_to(user)
            expenses = selectors.expenses_visible_to(user)
            ctx['expected_revenue'] = billing_selectors.expected_revenue(invoices)
            ctx['collected'] = billing_selectors.collected_amount(invoices)
            ctx['outstanding'] = billing_selectors.outstanding_amount(invoices)
            ctx['collection_rate'] = billing_selectors.collection_rate(invoices)
            ctx['total_expenses'] = expenses_selectors.valid_expense_total(expenses)
            ctx['net_profit'] = billing_selectors.profit(invoices, expenses)
            overdue = billing_selectors.overdue_invoices(invoices)
            if overdue:
                overdue_total = sum((billing_selectors.invoice_outstanding(i) for i in overdue), start=0)
                needs_attention.append({'label': 'Overdue invoices', 'count': len(overdue), 'meta': f'QAR {overdue_total} outstanding', 'url': reverse('billing:invoice_list') + '?status=overdue'})
            pending_handovers = selectors.cash_handovers_visible_to(user).filter(status=CashHandover.Status.SUBMITTED)
            if pending_handovers.exists():
                needs_attention.append({'label': 'Pending cash handovers', 'count': pending_handovers.count(), 'meta': 'Awaiting your confirmation', 'url': reverse('cash:handover_list') + '?status=submitted'})
        if staff_view:
            invoices = selectors.invoices_visible_to(user)
            pending_rent = billing_selectors.outstanding_amount(invoices)
            ctx['pending_rent_to_collect'] = pending_rent
            overdue_rent = billing_selectors.overdue_invoices(invoices)
            if overdue_rent:
                overdue_total = sum((billing_selectors.invoice_outstanding(i) for i in overdue_rent), start=Decimal('0.00'))
                needs_attention.append({'label': 'Overdue tenant rent', 'count': len(overdue_rent), 'meta': f'QAR {overdue_total} to collect', 'url': reverse('billing:invoice_list') + '?status=overdue'})

            staff_bills = selectors.expenses_visible_to(user).filter(status='active', paid_by='staff', paid_on__isnull=True)
            staff_bills_due = staff_bills.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            staff_bills_count = staff_bills.count()
            ctx['staff_bills_due'] = staff_bills_due
            ctx['staff_bills_count'] = staff_bills_count
            ctx['my_expenses_paid'] = cash_selectors.total_staff_paid_expenses(user)
            if staff_bills_count > 0:
                needs_attention.append({'label': 'Staff bills due (Utilities / Maintenance)', 'count': staff_bills_count, 'meta': f'QAR {staff_bills_due} to pay from cash', 'url': reverse('expenses:list') + '?due=unpaid&status=active'})
        else:
            expense_summary = expenses_selectors.due_summary(selectors.expenses_visible_to(user))
            buckets = (('overdue', 'Overdue expenses'), ('today', 'Expenses due today'), ('verify', 'Expenses to verify'))
            for bucket, label in buckets:
                if expense_summary[bucket]['count']:
                    needs_attention.append({'label': label, 'count': expense_summary[bucket]['count'], 'meta': f"QAR {expense_summary[bucket]['total']} to pay", 'url': reverse('expenses:list') + f'?due={bucket}&status=active'})
        if ctx['vacant_count'] > 0:
            needs_attention.append({'label': 'Vacant partitions', 'count': ctx['vacant_count'], 'meta': 'No tenant assigned', 'url': reverse('villas:partition_list') + '?occupancy=vacant'})
        my_unclaimed = cash_selectors.unclaimed_cash_payments(user) if not user.is_owner else cash_selectors.unclaimed_cash_payments(user).none()
        dashboard_url = reverse('dashboard:home')
        if my_unclaimed.exists():
            needs_attention.append({'label': 'Your cash ready to hand over', 'count': my_unclaimed.count(), 'meta': f'QAR {cash_selectors.outstanding_cash_for(user)} to submit', 'url': f"{reverse('cash:handover_submit')}?next={dashboard_url}"})
            quick_actions.append({'label': 'Submit Cash Handover', 'url': f"{reverse('cash:handover_submit')}?next={dashboard_url}"})
        if selectors.manageable_villas(user).filter(is_archived=False, business__is_archived=False, is_sold=False).exists():
            quick_actions.append({'label': '+ Add Expense', 'url': f"{reverse('expenses:create')}?next={dashboard_url}"})
        if user.is_owner:
            quick_actions.insert(0, {'label': '+ Add Business', 'url': f"{reverse('businesses:create')}?next={dashboard_url}"})
        if villas.exists():
            quick_actions.append({'label': 'Record Collection', 'url': reverse('billing:invoice_list')})
        ctx['needs_attention'] = needs_attention
        ctx['quick_actions'] = quick_actions
        outstanding_cash = cash_selectors.outstanding_cash_for(user)
        ctx['outstanding_cash'] = outstanding_cash
        ctx['abs_outstanding_cash'] = abs(outstanding_cash)
        ctx['is_cash_negative'] = outstanding_cash < 0
        ctx['pending_handover_amount'] = cash_selectors.pending_handover_amount(user)
        if ctx['can_view_financials'] and not staff_view:
            rows = cash_selectors.staff_accountability(user, ctx['year'], ctx['month'])
            ctx['cash_held_by_staff'] = sum((r['holding'] for r in rows), start=0)
            ctx['pending_staff_handovers'] = sum((r['awaiting_confirmation'] for r in rows), start=0)
        return ctx

@login_required
def global_search(request):
    q = request.GET.get('q', '').strip()
    results = {}
    if q:
        user = request.user
        if not selectors.is_field_staff(user):
            results['businesses'] = selectors.businesses_visible_to(user).filter(name__icontains=q)[:10]
            results['villas'] = selectors.villas_visible_to(user).filter(name__icontains=q)[:10]
            results['partitions'] = selectors.partitions_visible_to(user).filter(name__icontains=q)[:10]
        else:
            results['villas'] = selectors.villas_visible_to(user).filter(name__icontains=q, is_sold=False, is_archived=False)[:10]
            results['partitions'] = selectors.partitions_visible_to(user).filter(name__icontains=q, villa__is_sold=False, villa__is_archived=False)[:10]
        results['tenants'] = selectors.tenants_visible_to(user).filter(Q(name__icontains=q) | Q(mobile__icontains=q))[:10]
        results['invoices'] = selectors.invoices_visible_to(user).filter(invoice_number__icontains=q)[:10]
        if user.is_owner:
            from apps.accounts.models import User
            results['staff'] = User.objects.exclude(username='system').filter(username__icontains=q)[:10]
    return render(request, 'dashboard/search.html', {'q': q, 'results': results, 'breadcrumbs': [('Search', None)]})

@login_required
def export_financials_csv(request):
    user, today = request.user, date.today()
    s = parse_date(request.GET.get('start_date') or '') or today.replace(day=1)
    e = parse_date(request.GET.get('end_date') or '') or today
    s, e = min(s, e), max(s, e)

    villas = selectors.financial_villas(user) or selectors.villas_visible_to(user)
    v_id = request.GET.get('villa')
    target_villa = None
    if v_id and v_id.isdigit():
        filtered_villas = selectors.villas_visible_to(user).filter(pk=int(v_id))
        if filtered_villas.exists():
            villas = filtered_villas
            target_villa = filtered_villas.first()

    inv = selectors.invoices_visible_to(user).filter(partition__villa__in=villas)
    exp = selectors.expenses_visible_to(user).filter(villa__in=villas)
    pnl = billing_selectors.period_profit_loss(inv, exp, s, e) if villas.exists() else {}

    inc, exp_tot, net = pnl.get('income', Decimal('0.00')), pnl.get('expenses', Decimal('0.00')), pnl.get('net', Decimal('0.00'))
    distributable = max(Decimal('0.00'), net)

    title = f"Hilltop Accounting — Financial Summary Report ({target_villa.name})" if target_villa else "Hilltop Accounting — Financial Summary Report"
    rows = [
        [title],
        ['Date Range' if s != e else 'Date', f"{s.strftime('%d %b %Y')}" if s == e else f"{s.strftime('%d %b %Y')} to {e.strftime('%d %b %Y')}"],
        ['Exported By', user.get_full_name() or user.username],
    ]
    if target_villa:
        rows.append(['Villa', target_villa.name])

    sec = 1
    rows.extend([
        [],
        [f'{sec}. TOTAL INCOME, EXPENSE & PROFIT', 'Amount (QAR)'],
        ['Total Income', f"{inc:.2f}"],
        ['Total Expenses', f"{exp_tot:.2f}"],
        ['Net Profit', f"{net:.2f}"],
    ])
    sec += 1

    rows.extend([
        [],
        [f'{sec}. COLLECTION DETAILS', 'Amount (QAR)'],
        ['Staff Collected', f"{pnl.get('staff_collected', Decimal('0.00')):.2f}"],
        ['Admin Collected', f"{pnl.get('admin_collected', Decimal('0.00')):.2f}"],
        ['Total Collected', f"{inc:.2f}"],
    ])
    sec += 1

    rows.extend([
        [],
        [f'{sec}. EXPENSE DETAILS', 'Amount (QAR)'],
        ['Staff Spent Expenses', f"{pnl.get('staff_expenses', Decimal('0.00')):.2f}"],
        ['Admin Spent Expenses', f"{pnl.get('owner_expenses', Decimal('0.00')):.2f}"],
        ['Total Expenses', f"{exp_tot:.2f}"],
    ])
    sec += 1

    if user.is_owner:
        from apps.businesses.models import PartnerVillaShare
        shares = PartnerVillaShare.objects.filter(villa__in=villas, partner__is_active=True).select_related('partner', 'villa')
        pnl_by_v = billing_selectors.profit_loss_by_villa(inv, exp, s, e) if shares and distributable > 0 else {}
        
        partner_breakdown_rows = []
        tot_partner = Decimal('0.00')
        for sh in shares:
            v_net = pnl_by_v.get(sh.villa_id, {}).get('net', Decimal('0.00'))
            cut = (v_net * (sh.percentage / Decimal('100.00'))).quantize(Decimal('0.01')) if v_net > 0 else Decimal('0.00')
            tot_partner += cut
            partner_breakdown_rows.append([
                sh.partner.name,
                sh.villa.name,
                f"{v_net:.2f}",
                f"{sh.percentage:.2f}%",
                f"{cut:.2f}"
            ])

        owner_cut = max(Decimal('0.00'), distributable - tot_partner)

        rows.extend([
            [],
            [f'{sec}. PROFIT SHARE', 'Amount (QAR)'],
            ['Distributable Net Profit', f"{distributable:.2f}"],
            ['Owner Profit Share', f"{owner_cut:.2f}"],
            ['Total Partners Share', f"{tot_partner:.2f}"],
        ])
        sec += 1

        rows.extend([
            [],
            [f'{sec}. PROFIT SHARE DETAILS (PARTNERS BY VILLA)'],
            ['Partner Name', 'Assigned Villa', 'Villa Net Profit (QAR)', 'Assigned Share %', 'Partner Cut (QAR)'],
        ])
        sec += 1
        if partner_breakdown_rows:
            rows.extend(partner_breakdown_rows)
        else:
            rows.append(['No active partner shares (100% Owner)'])

    if not target_villa:
        from apps.billing.models import Payment
        from apps.expenses.models import Expense

        live_inv = inv.filter(is_cancelled=False)
        pay_rows = Payment.objects.filter(invoice__in=live_inv, is_cancelled=False, collected_at__gte=s, collected_at__lte=e).values('invoice__partition__villa_id', 'collected_by__is_owner').annotate(total=Sum('amount'))
        exp_rows = exp.filter(status=Expense.Status.ACTIVE, date__gte=s, date__lte=e).values('villa_id', 'paid_by').annotate(total=Sum('amount'))

        villa_map = {
            v.id: {'name': v.name, 'income': Decimal('0.00'), 'expenses': Decimal('0.00'), 'staff_c': Decimal('0.00'), 'admin_c': Decimal('0.00'), 'staff_e': Decimal('0.00'), 'admin_e': Decimal('0.00')}
            for v in villas
        }
        for r in pay_rows:
            vid = r['invoice__partition__villa_id']
            if vid in villa_map:
                amt = r['total'] or Decimal('0.00')
                villa_map[vid]['income'] += amt
                if r['collected_by__is_owner']:
                    villa_map[vid]['admin_c'] += amt
                else:
                    villa_map[vid]['staff_c'] += amt
        for r in exp_rows:
            vid = r['villa_id']
            if vid in villa_map:
                amt = r['total'] or Decimal('0.00')
                villa_map[vid]['expenses'] += amt
                if r['paid_by'] == 'staff':
                    villa_map[vid]['staff_e'] += amt
                else:
                    villa_map[vid]['admin_e'] += amt

        villa_rows = [
            [
                d['name'],
                f"{d['income']:.2f}",
                f"{d['expenses']:.2f}",
                f"{(d['income'] - d['expenses']):.2f}",
                f"{d['staff_c']:.2f}",
                f"{d['admin_c']:.2f}",
                f"{d['staff_e']:.2f}",
                f"{d['admin_e']:.2f}"
            ]
            for d in villa_map.values()
        ]

        rows.extend([
            [],
            [f'{sec}. VILLA FINANCIAL BREAKDOWN'],
            ['Villa Name', 'Total Income (QAR)', 'Total Expenses (QAR)', 'Net Profit (QAR)', 'Staff Collected (QAR)', 'Admin Collected (QAR)', 'Staff Spent (QAR)', 'Admin Spent (QAR)'],
            *villa_rows,
            ['Total', f"{inc:.2f}", f"{exp_tot:.2f}", f"{net:.2f}", f"{pnl.get('staff_collected', Decimal('0.00')):.2f}", f"{pnl.get('admin_collected', Decimal('0.00')):.2f}", f"{pnl.get('staff_expenses', Decimal('0.00')):.2f}", f"{pnl.get('owner_expenses', Decimal('0.00')):.2f}"],
        ])
        sec += 1

    from apps.audit import services as audit_services
    from apps.audit.models import Action
    audit_services.log(user=user, action=Action.REPORT_EXPORTED, reason=f"Exported financial summary CSV for {s} to {e}" + (f" ({target_villa.name})" if target_villa else ""))

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response.write('\ufeff')
    v_tag = f"_{target_villa.name.replace(' ', '_')}" if target_villa else ""
    d_tag = f"{s:%Y%m%d}" if s == e else f"{s:%Y%m%d}_{e:%Y%m%d}"
    response['Content-Disposition'] = f'attachment; filename="financial_summary{v_tag}_{d_tag}.csv"'
    csv.writer(response).writerows(rows)
    return response
