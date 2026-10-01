from datetime import date, timedelta
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User
from apps.accounts.services import assign_business_manager, assign_villa_staff
from apps.billing.models import ChargeType, Invoice, Payment
from apps.billing.services import create_charge, generate_monthly_invoice, record_payment
from apps.businesses.services import create_business
from apps.cash.models import CashHandover
from apps.cash.services import reject_cash_handover, submit_cash_handover
from apps.expenses.models import Expense
from apps.expenses.services import create_expense, get_or_create_category, mark_expense_paid
from apps.tenancy.models import Tenant
from apps.tenancy.services import create_tenant
from apps.villas.services import create_partition, create_villa
from .models import Notification
from .services import sync_for_user

PASSWORD = 'Passw0rd!2026'

class NotificationBase(TestCase):

    def setUp(self):
        self.today = date.today()
        self.owner = User.objects.create_user('owner', password=PASSWORD, is_owner=True)
        self.business_a = create_business(name='Business A', created_by=self.owner)
        self.business_b = create_business(name='Business B', created_by=self.owner)
        self.villa_a = create_villa(business=self.business_a, name='Villa A', created_by=self.owner)
        self.villa_b = create_villa(business=self.business_b, name='Villa B', created_by=self.owner)
        self.staff_a = User.objects.create_user('staff_a', password=PASSWORD)
        self.staff_b = User.objects.create_user('staff_b', password=PASSWORD)
        self.manager_a = User.objects.create_user('manager_a', password=PASSWORD)
        assign_villa_staff(user=self.staff_a, villa=self.villa_a, assigned_by=self.owner)
        assign_villa_staff(user=self.staff_b, villa=self.villa_b, assigned_by=self.owner)
        assign_business_manager(user=self.manager_a, business=self.business_a, assigned_by=self.owner)
        self.category = get_or_create_category(name='Electricity')

    def expense(self, villa, due_in_days, **kwargs):
        return create_expense(villa=villa, category=self.category, amount=Decimal('450.00'), date=self.today - timedelta(days=30), due_date=self.today + timedelta(days=due_in_days), created_by=self.owner, **kwargs)

    def kinds_for(self, user):
        sync_for_user(user)
        return set(Notification.objects.filter(recipient=user, resolved_at__isnull=True).values_list('kind', flat=True))

class ExpenseNotificationTests(NotificationBase):

    def test_overdue_and_due_soon_expenses_notify_only_authorized_users(self):
        overdue = self.expense(self.villa_a, -2)
        self.expense(self.villa_a, 2)
        self.assertIn(Notification.Kind.EXPENSE_OVERDUE, self.kinds_for(self.owner))
        self.assertIn(Notification.Kind.EXPENSE_DUE_SOON, self.kinds_for(self.owner))
        self.assertIn(Notification.Kind.EXPENSE_OVERDUE, self.kinds_for(self.staff_a))
        self.assertIn(Notification.Kind.EXPENSE_OVERDUE, self.kinds_for(self.manager_a))
        for outsider in (self.staff_b,):
            self.assertFalse({Notification.Kind.EXPENSE_OVERDUE, Notification.Kind.EXPENSE_DUE_SOON} & self.kinds_for(outsider))
        link = Notification.objects.get(recipient=self.staff_a, kind=Notification.Kind.EXPENSE_OVERDUE).url
        self.assertEqual(link, reverse('expenses:mark_paid', args=[overdue.pk]))

    def test_far_future_and_undated_expenses_do_not_notify(self):
        self.expense(self.villa_a, 30)
        create_expense(villa=self.villa_a, category=self.category, amount=Decimal('10.00'), date=self.today, created_by=self.owner)
        self.assertFalse(self.kinds_for(self.owner) & {Notification.Kind.EXPENSE_OVERDUE, Notification.Kind.EXPENSE_DUE_SOON})

    def test_paying_the_expense_resolves_the_notification(self):
        expense = self.expense(self.villa_a, -1)
        self.assertIn(Notification.Kind.EXPENSE_OVERDUE, self.kinds_for(self.owner))
        mark_expense_paid(expense=expense, paid_on=self.today, payment_method='cash', marked_by=self.owner)
        self.assertNotIn(Notification.Kind.EXPENSE_OVERDUE, self.kinds_for(self.owner))

    def test_sync_is_idempotent(self):
        self.expense(self.villa_a, -1)
        sync_for_user(self.owner)
        sync_for_user(self.owner)
        self.assertEqual(Notification.objects.filter(recipient=self.owner, kind=Notification.Kind.EXPENSE_OVERDUE).count(), 1)

class OperationalNotificationTests(NotificationBase):

    def _invoice_for(self, villa, partition_name, due_date):
        partition = create_partition(villa=villa, name=partition_name, created_by=self.owner)
        create_tenant(partition=partition, name=f'Tenant {partition_name}', move_in_date=date(2026, 1, 1), monthly_rent=Decimal('1000.00'), created_by=self.owner)
        create_charge(partition=partition, charge_type=ChargeType.objects.get_or_create(name='Rent')[0], amount=Decimal('1000.00'), start_date=date(2026, 1, 1), created_by=self.owner)
        return generate_monthly_invoice(partition=partition, billing_period_start=date(2026, 2, 1), billing_period_end=date(2026, 2, 28), issue_date=date(2026, 2, 1), due_date=due_date, generated_by=self.owner)

    def test_overdue_invoice_notifies_only_its_own_scope(self):
        invoice = self._invoice_for(self.villa_a, 'A1', date(2026, 2, 10))
        self.assertIn(Notification.Kind.INVOICE_OVERDUE, self.kinds_for(self.staff_a))
        self.assertIn(Notification.Kind.INVOICE_OVERDUE, self.kinds_for(self.owner))
        self.assertNotIn(Notification.Kind.INVOICE_OVERDUE, self.kinds_for(self.staff_b))
        self.assertEqual(Notification.objects.get(recipient=self.staff_a, kind=Notification.Kind.INVOICE_OVERDUE).url, reverse('billing:invoice_detail', args=[invoice.pk]))

    def test_vacant_partition_notifies_scope_and_clears_on_move_in(self):
        partition = create_partition(villa=self.villa_a, name='Empty', created_by=self.owner)
        self.assertIn(Notification.Kind.VACANT_PARTITION, self.kinds_for(self.staff_a))
        self.assertNotIn(Notification.Kind.VACANT_PARTITION, self.kinds_for(self.staff_b))
        create_tenant(partition=partition, name='New Tenant', move_in_date=self.today, monthly_rent=Decimal('500.00'), created_by=self.owner)
        self.assertNotIn(Notification.Kind.VACANT_PARTITION, self.kinds_for(self.staff_a))

    def test_contract_expiry_goes_to_owner_and_business_manager_only(self):
        self.villa_a.contract_end = self.today + timedelta(days=20)
        self.villa_a.save(update_fields=['contract_end'])
        self.assertIn(Notification.Kind.CONTRACT_EXPIRING, self.kinds_for(self.owner))
        self.assertIn(Notification.Kind.CONTRACT_EXPIRING, self.kinds_for(self.manager_a))
        self.assertNotIn(Notification.Kind.CONTRACT_EXPIRING, self.kinds_for(self.staff_a))
        self.assertNotIn(Notification.Kind.CONTRACT_EXPIRING, self.kinds_for(self.staff_b))

    def test_collection_recorded_notifies_owner_and_manager_but_not_actor_or_outsiders(self):
        invoice = self._invoice_for(self.villa_a, 'A1', date(2026, 12, 31))
        record_payment(invoice=invoice, amount=Decimal('400.00'), method='cash', collected_by=self.staff_a, collected_at=self.today, created_by=self.staff_a)
        kinds = lambda u: set(Notification.objects.filter(recipient=u).values_list('kind', flat=True))
        self.assertIn(Notification.Kind.COLLECTION_RECORDED, kinds(self.owner))
        self.assertIn(Notification.Kind.COLLECTION_RECORDED, kinds(self.manager_a))
        self.assertNotIn(Notification.Kind.COLLECTION_RECORDED, kinds(self.staff_a))
        self.assertNotIn(Notification.Kind.COLLECTION_RECORDED, kinds(self.staff_b))

    def test_handover_pending_for_reviewers_and_rejected_for_the_staff_member(self):
        invoice = self._invoice_for(self.villa_a, 'A1', date(2026, 12, 31))
        payment = record_payment(invoice=invoice, amount=Decimal('400.00'), method='cash', collected_by=self.staff_a, collected_at=self.today, created_by=self.staff_a)
        handover = submit_cash_handover(staff=self.staff_a, payments=[payment], submitted_by=self.staff_a)
        self.assertIn(Notification.Kind.CASH_HANDOVER_PENDING, self.kinds_for(self.owner))
        self.assertIn(Notification.Kind.CASH_HANDOVER_PENDING, self.kinds_for(self.manager_a))
        self.assertNotIn(Notification.Kind.CASH_HANDOVER_PENDING, self.kinds_for(self.staff_a))
        self.assertNotIn(Notification.Kind.CASH_HANDOVER_PENDING, self.kinds_for(self.staff_b))
        reject_cash_handover(handover=handover, rejected_by=self.owner, reason='Count is off')
        self.assertIn(Notification.Kind.CASH_HANDOVER_REJECTED, self.kinds_for(self.staff_a))
        self.assertNotIn(Notification.Kind.CASH_HANDOVER_PENDING, self.kinds_for(self.owner))
        self.assertNotIn(Notification.Kind.CASH_HANDOVER_REJECTED, self.kinds_for(self.staff_b))

class NotificationHttpTests(NotificationBase):

    def test_unread_badge_list_and_mark_read_over_http(self):
        self.expense(self.villa_a, -1)
        self.assertTrue(self.client.login(username='staff_a', password=PASSWORD))
        r = self.client.get(reverse('dashboard:home'))
        self.assertEqual(r.context['notification_unread_count'], 1)
        r = self.client.get(reverse('notifications:list'))
        self.assertContains(r, 'Electricity overdue')
        notification = Notification.objects.get(recipient=self.staff_a)
        r = self.client.post(reverse('notifications:read', args=[notification.pk]))
        self.assertRedirects(r, reverse('notifications:list'))
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)
        self.assertEqual(self.client.get(reverse('notifications:list')).context['unread_total'], 0)

    def test_cannot_mark_another_users_notification_and_get_is_rejected(self):
        self.expense(self.villa_a, -1)
        sync_for_user(self.owner)
        owners = Notification.objects.get(recipient=self.owner)
        self.client.login(username='staff_b', password=PASSWORD)
        self.assertEqual(self.client.post(reverse('notifications:read', args=[owners.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('notifications:read', args=[owners.pk])).status_code, 405)
        self.assertEqual(self.client.get(reverse('notifications:read_all')).status_code, 405)
        self.assertNotContains(self.client.get(reverse('notifications:list')), 'Electricity')
        owners.refresh_from_db()
        self.assertIsNone(owners.read_at)

    def test_anonymous_is_redirected_to_login(self):
        r = self.client.get(reverse('notifications:list'))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse('accounts:login'), r['Location'])
