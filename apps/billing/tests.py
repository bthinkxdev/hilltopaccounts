from datetime import date, timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from apps.accounts.models import User
from apps.accounts.selectors import invoices_visible_to, payments_visible_to
from apps.accounts.services import assign_villa_staff
from apps.audit.models import Action, AuditLog
from apps.businesses.services import create_business
from apps.tenancy.services import create_tenant
from apps.villas.services import create_partition, create_villa
from . import selectors
from .exceptions import DuplicateInvoicePeriod, InvoiceCancelled, PartitionVacant, PaymentExceedsOutstanding
from .models import Invoice
from .services import cancel_invoice, cancel_payment, correct_payment, create_charge, generate_monthly_invoice, get_or_create_charge_type, record_payment
FAR_FUTURE_DUE_DATE = date.today() + timedelta(days=3650)

class BillingTestBase(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', is_owner=True)
        self.business = create_business(name='Business A', created_by=self.owner)
        self.villa = create_villa(business=self.business, name='Villa 1', created_by=self.owner)
        self.partition = create_partition(villa=self.villa, name='Unit 1', created_by=self.owner)
        self.tenant = create_tenant(partition=self.partition, name='Ahmed Ali', move_in_date=date(2026, 1, 1), monthly_rent=Decimal('3000.00'), created_by=self.owner)
        self.rent_type = get_or_create_charge_type(name='Rent')
        self.electricity_type = get_or_create_charge_type(name='Electricity')

    def _generate_invoice(self, **overrides):
        defaults = dict(partition=self.partition, billing_period_start=date(2026, 2, 1), billing_period_end=date(2026, 2, 28), issue_date=date(2026, 2, 1), due_date=FAR_FUTURE_DUE_DATE, generated_by=self.owner)
        defaults.update(overrides)
        return generate_monthly_invoice(**defaults)

class ChargeTests(BillingTestBase):

    def test_negative_amount_is_rejected(self):
        with self.assertRaises(ValidationError):
            create_charge(partition=self.partition, charge_type=self.rent_type, amount=Decimal('-100'), start_date=date(2026, 1, 1), created_by=self.owner)

    def test_end_date_before_start_date_is_rejected(self):
        with self.assertRaises(ValidationError):
            create_charge(partition=self.partition, charge_type=self.rent_type, amount=Decimal('100'), start_date=date(2026, 2, 1), end_date=date(2026, 1, 1), created_by=self.owner)

class InvoiceGenerationTests(BillingTestBase):

    def test_invoice_with_one_charge(self):
        create_charge(partition=self.partition, charge_type=self.rent_type, amount=Decimal('3000.00'), start_date=date(2026, 1, 1), created_by=self.owner)
        invoice = self._generate_invoice()
        self.assertEqual(invoice.items.count(), 1)
        self.assertEqual(selectors.invoice_total(invoice), Decimal('3000.00'))

    def test_invoice_with_multiple_charges(self):
        create_charge(partition=self.partition, charge_type=self.rent_type, amount=Decimal('3000.00'), start_date=date(2026, 1, 1), created_by=self.owner)
        create_charge(partition=self.partition, charge_type=self.electricity_type, amount=Decimal('250.50'), start_date=date(2026, 1, 1), created_by=self.owner)
        invoice = self._generate_invoice()
        self.assertEqual(invoice.items.count(), 2)
        self.assertEqual(selectors.invoice_total(invoice), Decimal('3250.50'))

    def test_inactive_charge_is_excluded(self):
        create_charge(partition=self.partition, charge_type=self.rent_type, amount=Decimal('3000.00'), start_date=date(2026, 1, 1), created_by=self.owner, is_active=False)
        invoice = self._generate_invoice()
        self.assertEqual(invoice.items.count(), 0)

    def test_charge_created_after_billing_period_is_excluded(self):
        create_charge(partition=self.partition, charge_type=self.rent_type, amount=Decimal('3000.00'), start_date=date(2026, 3, 1), created_by=self.owner)
        invoice = self._generate_invoice()
        self.assertEqual(invoice.items.count(), 0)

    def test_paid_one_time_charge_is_not_billed_again(self):
        wifi = get_or_create_charge_type(name='Wifi')
        create_charge(partition=self.partition, charge_type=wifi, amount=Decimal('5000.00'), start_date=date(2026, 1, 1), frequency='one_time', created_by=self.owner)
        first = self._generate_invoice()
        record_payment(invoice=first, amount=Decimal('5000.00'), method='cash', collected_by=self.owner, collected_at=date(2026, 2, 2), created_by=self.owner)
        create_charge(partition=self.partition, charge_type=self.electricity_type, amount=Decimal('6000.00'), start_date=date(2026, 1, 1), frequency='one_time', created_by=self.owner)
        second = self._generate_invoice(billing_period_start=date(2026, 3, 1), billing_period_end=date(2026, 3, 31))
        self.assertEqual(selectors.invoice_total(second), Decimal('6000.00'))

    def test_charge_on_cancelled_invoice_can_be_rebilled(self):
        create_charge(partition=self.partition, charge_type=self.rent_type, amount=Decimal('100.00'), start_date=date(2026, 1, 1), frequency='one_time', created_by=self.owner)
        cancel_invoice(invoice=self._generate_invoice(), cancelled_by=self.owner)
        second = self._generate_invoice(billing_period_start=date(2026, 3, 1), billing_period_end=date(2026, 3, 31))
        self.assertEqual(second.items.count(), 1)

    def test_monthly_charge_recurs_across_periods_but_not_within_one(self):
        create_charge(partition=self.partition, charge_type=self.rent_type, amount=Decimal('3000.00'), start_date=date(2026, 1, 1), frequency='monthly', created_by=self.owner)
        self._generate_invoice()
        march = self._generate_invoice(billing_period_start=date(2026, 3, 1), billing_period_end=date(2026, 3, 31))
        self.assertEqual(march.items.count(), 1)
        overlap = self._generate_invoice(billing_period_start=date(2026, 3, 15), billing_period_end=date(2026, 4, 14))
        self.assertEqual(overlap.items.count(), 0)

    def test_cannot_bill_a_vacant_partition(self):
        vacant = create_partition(villa=self.villa, name='Unit 2', created_by=self.owner)
        with self.assertRaises(PartitionVacant):
            generate_monthly_invoice(partition=vacant, billing_period_start=date(2026, 2, 1), billing_period_end=date(2026, 2, 28), issue_date=date(2026, 2, 1), due_date=FAR_FUTURE_DUE_DATE, generated_by=self.owner)

    def test_duplicate_billing_period_is_rejected(self):
        self._generate_invoice()
        with self.assertRaises(DuplicateInvoicePeriod):
            self._generate_invoice()

    def test_invoice_generation_is_audited(self):
        self._generate_invoice()
        self.assertTrue(AuditLog.objects.filter(action=Action.INVOICE_CREATED).exists())

class PaymentTests(BillingTestBase):

    def setUp(self):
        super().setUp()
        create_charge(partition=self.partition, charge_type=self.rent_type, amount=Decimal('3000.00'), start_date=date(2026, 1, 1), created_by=self.owner)
        self.invoice = self._generate_invoice()

    def _pay(self, amount, **overrides):
        defaults = dict(invoice=self.invoice, amount=amount, method='cash', collected_by=self.owner, collected_at=date(2026, 2, 5), created_by=self.owner)
        defaults.update(overrides)
        return record_payment(**defaults)

    def test_full_payment_marks_invoice_paid(self):
        self._pay(Decimal('3000.00'))
        self.assertEqual(selectors.invoice_status(self.invoice), Invoice.Status.PAID)
        self.assertEqual(selectors.invoice_outstanding(self.invoice), Decimal('0.00'))

    def test_partial_payment_marks_invoice_partially_paid(self):
        self._pay(Decimal('1000.00'))
        self.assertEqual(selectors.invoice_status(self.invoice), Invoice.Status.PARTIALLY_PAID)
        self.assertEqual(selectors.invoice_outstanding(self.invoice), Decimal('2000.00'))

    def test_multiple_payments_accumulate(self):
        self._pay(Decimal('1000.00'))
        self._pay(Decimal('1500.00'))
        self.assertEqual(selectors.invoice_paid_amount(self.invoice), Decimal('2500.00'))
        self.assertEqual(selectors.invoice_outstanding(self.invoice), Decimal('500.00'))

    def test_unpaid_invoice_past_due_date_is_overdue(self):
        self.invoice.due_date = date.today() - timedelta(days=1)
        self.invoice.save(update_fields=['due_date'])
        self.assertEqual(selectors.invoice_status(self.invoice), Invoice.Status.OVERDUE)

    def test_fully_paid_invoice_past_due_date_is_paid_not_overdue(self):
        self._pay(Decimal('3000.00'))
        self.invoice.due_date = date.today() - timedelta(days=1)
        self.invoice.save(update_fields=['due_date'])
        self.assertEqual(selectors.invoice_status(self.invoice), Invoice.Status.PAID)

    def test_overpayment_is_rejected(self):
        with self.assertRaises(PaymentExceedsOutstanding):
            self._pay(Decimal('5000.00'))

    def test_zero_and_negative_payments_are_rejected(self):
        with self.assertRaises(ValidationError):
            self._pay(Decimal('0.00'))
        with self.assertRaises(ValidationError):
            self._pay(Decimal('-100.00'))

    def test_cannot_pay_a_cancelled_invoice(self):
        cancel_invoice(invoice=self.invoice, cancelled_by=self.owner, reason='tenant dispute')
        with self.assertRaises(InvoiceCancelled):
            self._pay(Decimal('100.00'))

    def test_cancelled_invoice_has_zero_outstanding_and_does_not_affect_totals(self):
        cancel_invoice(invoice=self.invoice, cancelled_by=self.owner, reason='voided')
        self.assertEqual(selectors.invoice_outstanding(self.invoice), Decimal('0.00'))
        self.assertEqual(selectors.expected_revenue(Invoice.objects.filter(pk=self.invoice.pk)), Decimal('0.00'))

    def test_cancel_payment_reduces_paid_amount_without_editing_the_row(self):
        payment = self._pay(Decimal('1000.00'))
        original_amount = payment.amount
        cancel_payment(payment=payment, cancelled_by=self.owner, reason='wrong tenant')
        payment.refresh_from_db()
        self.assertEqual(payment.amount, original_amount)
        self.assertTrue(payment.is_cancelled)
        self.assertEqual(selectors.invoice_paid_amount(self.invoice), Decimal('0.00'))

    def test_cancel_payment_is_audited(self):
        payment = self._pay(Decimal('1000.00'))
        cancel_payment(payment=payment, cancelled_by=self.owner, reason='duplicate entry')
        entry = AuditLog.objects.get(action=Action.COLLECTION_CANCELLED)
        self.assertEqual(entry.reason, 'duplicate entry')

    def test_correct_payment_creates_new_row_and_preserves_original(self):
        payment = self._pay(Decimal('1000.00'))
        corrected = correct_payment(payment=payment, new_amount=Decimal('1500.00'), corrected_by=self.owner, reason='wrong amount entered')
        payment.refresh_from_db()
        self.assertTrue(payment.is_cancelled)
        self.assertEqual(payment.amount, Decimal('1000.00'))
        self.assertFalse(corrected.is_cancelled)
        self.assertEqual(corrected.amount, Decimal('1500.00'))
        self.assertEqual(corrected.corrects_id, payment.pk)
        self.assertEqual(selectors.invoice_paid_amount(self.invoice), Decimal('1500.00'))

    def test_duplicate_submission_is_prevented_by_double_click_guard(self):
        self._pay(Decimal('3000.00'))
        with self.assertRaises(PaymentExceedsOutstanding):
            self._pay(Decimal('3000.00'))
        self.assertEqual(self.invoice.payments.count(), 1)

class FinancialEngineTests(BillingTestBase):

    def setUp(self):
        super().setUp()
        create_charge(partition=self.partition, charge_type=self.rent_type, amount=Decimal('2000.00'), start_date=date(2026, 1, 1), created_by=self.owner)
        self.invoice = self._generate_invoice()

    def test_expected_revenue_and_collected_and_outstanding(self):
        record_payment(invoice=self.invoice, amount=Decimal('500.00'), method='cash', collected_by=self.owner, collected_at=date(2026, 2, 5), created_by=self.owner)
        qs = Invoice.objects.filter(pk=self.invoice.pk)
        self.assertEqual(selectors.expected_revenue(qs), Decimal('2000.00'))
        self.assertEqual(selectors.collected_amount(qs), Decimal('500.00'))
        self.assertEqual(selectors.outstanding_amount(qs), Decimal('1500.00'))

    def test_collection_rate_handles_zero_denominator(self):
        empty_qs = Invoice.objects.none()
        self.assertEqual(selectors.collection_rate(empty_qs), Decimal('0.00'))

    def test_collection_rate_is_percentage(self):
        record_payment(invoice=self.invoice, amount=Decimal('1000.00'), method='cash', collected_by=self.owner, collected_at=date(2026, 2, 5), created_by=self.owner)
        qs = Invoice.objects.filter(pk=self.invoice.pk)
        self.assertEqual(selectors.collection_rate(qs), Decimal('50.00'))

class BillingRBACTests(BillingTestBase):

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user('staff')
        self.other_villa = create_villa(business=self.business, name='Villa 2', created_by=self.owner)
        self.other_partition = create_partition(villa=self.other_villa, name='Unit 1', created_by=self.owner)
        self.other_tenant = create_tenant(partition=self.other_partition, name='Other Tenant', move_in_date=date(2026, 1, 1), monthly_rent=Decimal('1000.00'), created_by=self.owner)
        assign_villa_staff(user=self.staff, villa=self.villa, assigned_by=self.owner)
        create_charge(partition=self.partition, charge_type=self.rent_type, amount=Decimal('3000.00'), start_date=date(2026, 1, 1), created_by=self.owner)
        create_charge(partition=self.other_partition, charge_type=self.rent_type, amount=Decimal('1000.00'), start_date=date(2026, 1, 1), created_by=self.owner)
        self.invoice = self._generate_invoice()
        self.other_invoice = generate_monthly_invoice(partition=self.other_partition, billing_period_start=date(2026, 2, 1), billing_period_end=date(2026, 2, 28), issue_date=date(2026, 2, 1), due_date=FAR_FUTURE_DUE_DATE, generated_by=self.owner)
        self.payment = record_payment(invoice=self.invoice, amount=Decimal('500.00'), method='cash', collected_by=self.staff, collected_at=date(2026, 2, 5), created_by=self.staff)
        record_payment(invoice=self.other_invoice, amount=Decimal('200.00'), method='cash', collected_by=self.owner, collected_at=date(2026, 2, 5), created_by=self.owner)

    def test_staff_sees_only_invoices_for_assigned_villa(self):
        visible = invoices_visible_to(self.staff)
        self.assertQuerySetEqual(visible, [self.invoice], transform=lambda i: i)

    def test_staff_sees_only_payments_for_assigned_villa(self):
        visible = payments_visible_to(self.staff)
        self.assertQuerySetEqual(visible, [self.payment], transform=lambda p: p)

    def test_owner_sees_everything(self):
        self.assertEqual(invoices_visible_to(self.owner).count(), 2)
        self.assertEqual(payments_visible_to(self.owner).count(), 2)
