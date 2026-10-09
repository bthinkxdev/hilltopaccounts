from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from decimal import Decimal
from django.db.models import Q, Sum
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
        villas = selectors.villas_visible_to(user).filter(is_archived=False, business__is_archived=False).select_related('business')
        partitions = selectors.partitions_visible_to(user).filter(status='active', villa__is_archived=False, villa__business__is_archived=False)
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
        ctx['period_pnl'] = billing_selectors.period_profit_loss(selectors.invoices_visible_to(user).filter(partition__villa__in=financial), selectors.expenses_visible_to(user).filter(villa__in=financial), ctx['period_start'], ctx['period_end']) if financial.exists() else None
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
        if selectors.manageable_villas(user).filter(is_archived=False, business__is_archived=False).exists():
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
        results['tenants'] = selectors.tenants_visible_to(user).filter(Q(name__icontains=q) | Q(mobile__icontains=q))[:10]
        results['invoices'] = selectors.invoices_visible_to(user).filter(invoice_number__icontains=q)[:10]
        if user.is_owner:
            from apps.accounts.models import User
            results['staff'] = User.objects.exclude(username='system').filter(username__icontains=q)[:10]
    return render(request, 'dashboard/search.html', {'q': q, 'results': results, 'breadcrumbs': [('Search', None)]})
