from dataclasses import dataclass
from datetime import date, timedelta
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from apps.accounts import selectors
from apps.accounts.models import Assignment, User
from apps.billing import selectors as billing_selectors
from apps.cash.models import CashHandover
from apps.expenses.models import Expense
from apps.tenancy.models import Tenant
from apps.villas.models import Partition, Villa
from .models import Notification

EXPENSE_DUE_SOON_DAYS = 3
CONTRACT_EXPIRY_WINDOW_DAYS = 60
REJECTED_HANDOVER_WINDOW_DAYS = 30
# State-based kinds are recomputed from live data on every sync and auto-resolve when the condition clears.
# Event kinds (a collection being recorded) are written once, at the moment of the event.
STATE_KINDS = [kind for kind in Notification.Kind.values if kind != Notification.Kind.COLLECTION_RECORDED]

@dataclass(frozen=True)
class Candidate:
    kind: str
    dedupe_key: str
    title: str
    body: str
    url: str

def _expense_candidates(user, today):
    manageable = set(selectors.manageable_villas(user).values_list('pk', flat=True))
    expenses = selectors.expenses_visible_to(user).filter(status=Expense.Status.ACTIVE, paid_on__isnull=True, due_date__isnull=False, due_date__lte=today + timedelta(days=EXPENSE_DUE_SOON_DAYS)).select_related('villa', 'business', 'category')
    for expense in expenses:
        overdue = expense.due_date < today
        kind = Notification.Kind.EXPENSE_OVERDUE if overdue else Notification.Kind.EXPENSE_DUE_SOON
        where = expense.villa.name if expense.villa_id else expense.business.name
        url = reverse('expenses:mark_paid', args=[expense.pk]) if expense.villa_id in manageable else reverse('expenses:list') + f'?villa={expense.villa_id or ""}&status=active'
        yield Candidate(kind, f'{kind}:{expense.pk}', f'{expense.category} {"overdue" if overdue else "due " + ("today" if expense.due_date == today else "soon")} — QAR {expense.amount}', f'{where} · due {expense.due_date:%d %b %Y}', url)

def _invoice_candidates(user, today):
    overdue = billing_selectors.filter_by_status(selectors.invoices_visible_to(user), 'overdue').select_related('partition__villa', 'tenant')
    for invoice in overdue:
        yield Candidate(Notification.Kind.INVOICE_OVERDUE, f'invoice_overdue:{invoice.pk}', f'Invoice {invoice.invoice_number} overdue — QAR {invoice._total - invoice._paid}', f'{invoice.tenant.name} · {invoice.partition.villa.name} · due {invoice.due_date:%d %b %Y}', reverse('billing:invoice_detail', args=[invoice.pk]))

def _is_handover_reviewer(user) -> bool:
    return user.is_owner or Assignment.objects.filter(user=user, role__in=[Assignment.Role.BUSINESS_MANAGER, Assignment.Role.ACCOUNTANT]).exists()

def _handover_candidates(user, today):
    if _is_handover_reviewer(user):
        pending = selectors.cash_handovers_visible_to(user).filter(status=CashHandover.Status.SUBMITTED).exclude(staff=user).select_related('staff')
        for handover in pending:
            yield Candidate(Notification.Kind.CASH_HANDOVER_PENDING, f'cash_handover_pending:{handover.pk}', f'Cash handover #{handover.pk} awaiting confirmation — QAR {handover.declared_amount}', f'Submitted by {handover.staff.get_full_name() or handover.staff.username}', reverse('cash:handover_detail', args=[handover.pk]))
    cutoff = timezone.now() - timedelta(days=REJECTED_HANDOVER_WINDOW_DAYS)
    for handover in CashHandover.objects.filter(staff=user, status=CashHandover.Status.REJECTED, submitted_at__gte=cutoff):
        yield Candidate(Notification.Kind.CASH_HANDOVER_REJECTED, f'cash_handover_rejected:{handover.pk}', f'Cash handover #{handover.pk} was rejected', handover.rejection_reason[:280], reverse('cash:handover_detail', args=[handover.pk]))

def _managed_villas(user):
    if user.is_owner:
        return Villa.objects.filter(is_archived=False)
    business_ids = Assignment.objects.filter(user=user, role=Assignment.Role.BUSINESS_MANAGER).values('business_id')
    return Villa.objects.filter(is_archived=False, business_id__in=business_ids)

def _contract_candidates(user, today):
    villas = _managed_villas(user).filter(contract_end__gte=today, contract_end__lte=today + timedelta(days=CONTRACT_EXPIRY_WINDOW_DAYS))
    for villa in villas:
        days = (villa.contract_end - today).days
        yield Candidate(Notification.Kind.CONTRACT_EXPIRING, f'contract_expiring:{villa.pk}:{villa.contract_end.isoformat()}', f'Contract for {villa.name} expires in {days} day{"s" if days != 1 else ""}', f'Ends {villa.contract_end:%d %b %Y}', reverse('villas:villa_detail', args=[villa.pk]))

def _vacancy_candidates(user, today):
    villas = selectors.manageable_villas(user).filter(is_archived=False)
    vacant = Partition.objects.filter(villa__in=villas, status=Partition.Status.ACTIVE).exclude(tenancies__status=Tenant.Status.ACTIVE).select_related('villa')
    for partition in vacant:
        yield Candidate(Notification.Kind.VACANT_PARTITION, f'vacant_partition:{partition.pk}', f'{partition.name} is vacant', partition.villa.name, reverse('villas:partition_detail', args=[partition.pk]))

def sync_for_user(user, today=None) -> None:
    """Bring this user's state-based notifications in line with live data, inside their own RBAC scope."""
    today = today or date.today()
    candidates = {}
    for builder in (_expense_candidates, _invoice_candidates, _handover_candidates, _contract_candidates, _vacancy_candidates):
        for candidate in builder(user, today):
            candidates[candidate.dedupe_key] = candidate
    now = timezone.now()
    with transaction.atomic():
        existing = {n.dedupe_key: n for n in Notification.objects.filter(recipient=user, kind__in=STATE_KINDS)}
        Notification.objects.bulk_create([Notification(recipient=user, kind=c.kind, title=c.title, body=c.body, url=c.url, dedupe_key=c.dedupe_key) for key, c in candidates.items() if key not in existing], ignore_conflicts=True)
        reopen = [n.pk for key, n in existing.items() if key in candidates and n.resolved_at is not None]
        if reopen:
            Notification.objects.filter(pk__in=reopen).update(resolved_at=None, read_at=None)
        resolve = [n.pk for key, n in existing.items() if key not in candidates and n.resolved_at is None]
        if resolve:
            Notification.objects.filter(pk__in=resolve).update(resolved_at=now)

def notify_collection_recorded(payment) -> None:
    """Event notification: tell the Owner and the villa's Business Managers that money came in (never the actor)."""
    invoice = payment.invoice
    villa = invoice.villa
    recipients = User.objects.filter(is_active=True).filter(Q(is_owner=True) | Q(assignments__role=Assignment.Role.BUSINESS_MANAGER, assignments__business_id=villa.business_id)).exclude(pk=payment.created_by_id).distinct()
    title = f'QAR {payment.amount} collected — {invoice.tenant.name}'
    body = f'{villa.name} · {invoice.invoice_number} · {payment.get_method_display()}'
    url = reverse('billing:invoice_detail', args=[invoice.pk])
    Notification.objects.bulk_create([Notification(recipient=user, kind=Notification.Kind.COLLECTION_RECORDED, title=title, body=body, url=url, dedupe_key=f'collection_recorded:{payment.pk}') for user in recipients], ignore_conflicts=True)

def unread_count(user) -> int:
    return Notification.objects.filter(recipient=user, read_at__isnull=True, resolved_at__isnull=True).count()

def mark_read(*, user, notification_id=None) -> int:
    qs = Notification.objects.filter(recipient=user, read_at__isnull=True)
    if notification_id is not None:
        qs = qs.filter(pk=notification_id)
    return qs.update(read_at=timezone.now())
