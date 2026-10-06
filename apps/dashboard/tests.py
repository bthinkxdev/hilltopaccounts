from datetime import date
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Assignment, User
from apps.audit.models import Action, AuditLog
from apps.billing.models import ChargeType, Invoice, Payment
from apps.businesses.models import Business
from apps.cash.models import CashHandover
from apps.expenses.models import Expense, ExpenseCategory
from apps.tenancy.models import Tenant
from apps.villas.models import Partition, Villa

class OwnerFullJourneyTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', password='Passw0rd!2026', is_owner=True)
        self.client.force_login(self.owner)

    def test_full_owner_workflow(self):
        r = self.client.get(reverse('dashboard:home'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'No businesses yet.')
        r = self.client.post(reverse('businesses:create'), {'name': 'Villa Rentals', 'description': 'Main line'})
        business = Business.objects.get(name='Villa Rentals')
        self.assertRedirects(r, reverse('businesses:detail', args=[business.pk]))
        r = self.client.post(reverse('villas:villa_create') + f'?business={business.pk}', {'business': business.pk, 'name': 'Villa A', 'address': 'Al Waab'})
        villa = Villa.objects.get(name='Villa A')
        self.assertRedirects(r, reverse('villas:villa_detail', args=[villa.pk]))
        r = self.client.post(reverse('villas:partition_create', args=[villa.pk]), {'name': 'Unit 1'})
        partition = Partition.objects.get(name='Unit 1', villa=villa)
        self.assertRedirects(r, reverse('villas:partition_detail', args=[partition.pk]))
        r = self.client.post(reverse('tenancy:create', args=[partition.pk]), {'name': 'Ahmed Ali', 'mobile': '+974 5555 1234', 'move_in_date': '2026-01-01', 'monthly_rent': '3000.00', 'deposit': '3000.00'})
        tenant = Tenant.objects.get(name='Ahmed Ali')
        self.assertRedirects(r, reverse('tenancy:detail', args=[tenant.pk]))
        r = self.client.post(reverse('billing:invoice_generate', args=[partition.pk]), {'billing_period_start': '2026-02-01', 'billing_period_end': '2026-02-28', 'issue_date': '2026-02-01', 'due_date': '2026-02-10'})
        invoice = Invoice.objects.get(partition=partition)
        self.assertRedirects(r, reverse('billing:invoice_detail', args=[invoice.pk]))
        r = self.client.get(reverse('billing:invoice_detail', args=[invoice.pk]))
        self.assertContains(r, '3000.00')
        r = self.client.post(reverse('billing:payment_record', args=[invoice.pk]), {'amount': '3000.00', 'method': 'cash', 'collected_at': '2026-02-05'})
        payment = Payment.objects.get(invoice=invoice)
        self.assertRedirects(r, reverse('billing:invoice_detail', args=[invoice.pk]))
        r = self.client.get(reverse('billing:invoice_detail', args=[invoice.pk]))
        self.assertContains(r, 'Paid')
        r = self.client.post(reverse('expenses:create_for_villa', args=[villa.pk]), {'villa': villa.pk, 'name': 'Maintenance', 'amount': '200.00', 'date': '2026-02-06'})
        self.assertRedirects(r, reverse('villas:villa_detail', args=[villa.pk]))
        expense = Expense.objects.get(business=business)
        r = self.client.get(reverse('dashboard:home'))
        self.assertContains(r, 'Financial summary')
        self.assertContains(r, 'QAR 3000')
        # Owner collections go straight to owner funds: no handover is possible.
        self.client.post(reverse('cash:handover_submit'), {'payments': [payment.pk]})
        self.assertFalse(CashHandover.objects.filter(staff=self.owner).exists())
        self.client.force_login(self.owner)
        r = self.client.get(reverse('audit:list'))
        self.assertEqual(r.status_code, 200)
        actions_logged = set(AuditLog.objects.values_list('action', flat=True))
        for expected in [Action.BUSINESS_CREATED, Action.VILLA_CREATED, Action.PARTITION_CREATED, Action.TENANT_CREATED, Action.CHARGE_CREATED, Action.INVOICE_CREATED, Action.COLLECTION_CREATED, Action.EXPENSE_CREATED]:
            self.assertIn(expected, actions_logged, f'{expected} was not audited')

class StaffJourneyAndAccessControlTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', is_owner=True)
        self.staff = User.objects.create_user('staff', password='Passw0rd!2026')
        self.other_staff = User.objects.create_user('other_staff', password='Passw0rd!2026')
        self.business = Business.objects.create(name='Business A', created_by=self.owner)
        self.villa = Villa.objects.create(business=self.business, name='Villa 1', created_by=self.owner)
        self.other_villa = Villa.objects.create(business=self.business, name='Villa 2', created_by=self.owner)
        self.partition = Partition.objects.create(villa=self.villa, name='Unit 1', created_by=self.owner)
        self.other_partition = Partition.objects.create(villa=self.other_villa, name='Unit 1', created_by=self.owner)
        Assignment.objects.create(user=self.staff, role=Assignment.Role.VILLA_STAFF, villa=self.villa, created_by=self.owner)
        self.client.force_login(self.staff)

    def test_staff_sees_assigned_villa_dashboard(self):
        r = self.client.get(reverse('dashboard:home'))
        self.assertContains(r, 'Villa 1')
        self.assertNotContains(r, 'Villa 2')
        self.assertNotContains(r, 'Financial summary')

    def test_staff_can_reach_own_villa_partition(self):
        r = self.client.get(reverse('villas:partition_detail', args=[self.partition.pk]))
        self.assertEqual(r.status_code, 200)

    def test_staff_cannot_reach_sibling_villa_partition_by_url(self):
        r = self.client.get(reverse('villas:partition_detail', args=[self.other_partition.pk]))
        self.assertEqual(r.status_code, 404)

    def test_staff_cannot_reach_sibling_villa_detail_by_url(self):
        r = self.client.get(reverse('villas:villa_detail', args=[self.other_villa.pk]))
        self.assertEqual(r.status_code, 404)

    def test_staff_cannot_access_staff_management(self):
        r = self.client.get(reverse('accounts:staff_list'))
        self.assertEqual(r.status_code, 403)

    def test_staff_cannot_access_audit_log(self):
        r = self.client.get(reverse('audit:list'))
        self.assertEqual(r.status_code, 403)

    def test_staff_can_move_in_tenant_and_record_collection(self):
        r = self.client.post(reverse('tenancy:create', args=[self.partition.pk]), {'name': 'Tenant X', 'move_in_date': date.today().isoformat(), 'monthly_rent': '1000.00'})
        tenant = Tenant.objects.get(name='Tenant X')
        self.assertRedirects(r, reverse('tenancy:detail', args=[tenant.pk]))
        self.client.post(reverse('billing:invoice_generate', args=[self.partition.pk]), {'billing_period_start': (date.today().replace(day=1)).isoformat(), 'billing_period_end': date.today().replace(day=28).isoformat(), 'issue_date': date.today().replace(day=1).isoformat(), 'due_date': date.today().replace(day=28).isoformat()})
        invoice = Invoice.objects.get(partition=self.partition)
        r = self.client.post(reverse('billing:payment_record', args=[invoice.pk]), {'amount': '1000.00', 'method': 'cash', 'collected_at': '2026-02-05'})
        self.assertRedirects(r, reverse('billing:invoice_detail', args=[invoice.pk]))
        payment = Payment.objects.get(invoice=invoice)
        self.assertEqual(payment.collected_by, self.staff)

    def test_staff_cannot_confirm_own_cash_handover(self):
        from apps.billing.services import generate_monthly_invoice, record_payment
        from apps.tenancy.services import create_tenant
        from apps.billing.services import create_charge
        tenant = create_tenant(partition=self.partition, name='T', move_in_date='2026-01-01', monthly_rent=1000, created_by=self.owner)
        invoice = generate_monthly_invoice(partition=self.partition, billing_period_start='2026-02-01', billing_period_end='2026-02-28', issue_date='2026-02-01', due_date='2026-02-10', generated_by=self.owner)
        payment = record_payment(invoice=invoice, amount=1000, method='cash', collected_by=self.staff, collected_at='2026-02-05', created_by=self.staff)
        r = self.client.post(reverse('cash:handover_submit'), {'payments': [payment.pk]})
        handover = CashHandover.objects.get(staff=self.staff)
        self.assertRedirects(r, reverse('cash:handover_detail', args=[handover.pk]))
        r = self.client.get(reverse('cash:handover_detail', args=[handover.pk]))
        self.assertNotContains(r, 'Confirm')
        r = self.client.post(reverse('cash:handover_confirm', args=[handover.pk]), {'confirmed_amount': '1000.00'})
        self.assertEqual(r.status_code, 403)
