from datetime import date, timedelta
from decimal import Decimal
from django.test import TestCase
from apps.accounts.models import User
from apps.accounts.selectors import cash_handovers_visible_to
from apps.accounts.services import assign_business_manager, assign_villa_staff
from apps.audit.models import Action, AuditLog
from apps.billing.services import create_charge, generate_monthly_invoice, get_or_create_charge_type, record_payment
from apps.businesses.services import create_business
from apps.tenancy.services import create_tenant
from apps.villas.services import create_partition, create_villa
from . import selectors
from .exceptions import HandoverNotSubmitted, InvalidHandoverPayment, SelfConfirmationNotAllowed
from .models import CashHandover
from .services import confirm_cash_handover, reject_cash_handover, submit_cash_handover
FAR_FUTURE_DUE_DATE = date.today() + timedelta(days=3650)

class CashTestBase(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', is_owner=True)
        self.staff = User.objects.create_user('staff')
        self.other_staff = User.objects.create_user('other_staff')
        self.business = create_business(name='Business A', created_by=self.owner)
        self.villa = create_villa(business=self.business, name='Villa 1', created_by=self.owner)
        assign_villa_staff(user=self.staff, villa=self.villa, assigned_by=self.owner)
        assign_villa_staff(user=self.other_staff, villa=self.villa, assigned_by=self.owner)
        self.rent_type = get_or_create_charge_type(name='Rent')

    def _collect(self, amount, *, staff=None, partition_suffix='1'):
        staff = staff or self.staff
        partition = create_partition(villa=self.villa, name=f'Unit {partition_suffix}', created_by=self.owner)
        create_tenant(partition=partition, name=f'Tenant {partition_suffix}', move_in_date=date(2026, 1, 1), monthly_rent=amount, created_by=self.owner)
        create_charge(partition=partition, charge_type=self.rent_type, amount=amount, start_date=date(2026, 1, 1), created_by=self.owner)
        invoice = generate_monthly_invoice(partition=partition, billing_period_start=date(2026, 2, 1), billing_period_end=date(2026, 2, 28), issue_date=date(2026, 2, 1), due_date=FAR_FUTURE_DUE_DATE, generated_by=self.owner)
        return record_payment(invoice=invoice, amount=amount, method='cash', collected_by=staff, collected_at=date(2026, 2, 5), created_by=staff)

class StaffCashAccountabilityTests(CashTestBase):

    def test_collected_handed_over_discrepancy_scenario(self):
        self._collect(Decimal('8500.00'))
        handover = submit_cash_handover(staff=self.staff, payments=selectors.unclaimed_cash_payments(self.staff), submitted_by=self.staff)
        self.assertEqual(handover.declared_amount, Decimal('8500.00'))
        confirm_cash_handover(handover=handover, confirmed_amount=Decimal('8000.00'), confirmed_by=self.owner)
        handover.refresh_from_db()
        self.assertEqual(handover.discrepancy, Decimal('500.00'))
        self.assertEqual(selectors.outstanding_cash_for(self.staff), Decimal('500.00'))

    def test_full_handover_leaves_zero_outstanding(self):
        self._collect(Decimal('3000.00'))
        handover = submit_cash_handover(staff=self.staff, payments=selectors.unclaimed_cash_payments(self.staff), submitted_by=self.staff)
        confirm_cash_handover(handover=handover, confirmed_amount=Decimal('3000.00'), confirmed_by=self.owner)
        self.assertEqual(handover.discrepancy, Decimal('0.00'))
        self.assertEqual(selectors.outstanding_cash_for(self.staff), Decimal('0.00'))

    def test_pending_submitted_handover_does_not_reduce_outstanding(self):
        self._collect(Decimal('1000.00'))
        submit_cash_handover(staff=self.staff, payments=selectors.unclaimed_cash_payments(self.staff), submitted_by=self.staff)
        self.assertEqual(selectors.outstanding_cash_for(self.staff), Decimal('1000.00'))
        self.assertEqual(selectors.pending_handover_amount(self.staff), Decimal('1000.00'))

    def test_rejected_handover_frees_payment_for_a_new_handover(self):
        self._collect(Decimal('1000.00'))
        handover = submit_cash_handover(staff=self.staff, payments=selectors.unclaimed_cash_payments(self.staff), submitted_by=self.staff)
        reject_cash_handover(handover=handover, rejected_by=self.owner, reason='amount mismatch on count')
        handover.refresh_from_db()
        self.assertEqual(handover.status, CashHandover.Status.REJECTED)
        self.assertEqual(selectors.unclaimed_cash_payments(self.staff).count(), 1)
        new_handover = submit_cash_handover(staff=self.staff, payments=selectors.unclaimed_cash_payments(self.staff), submitted_by=self.staff)
        self.assertEqual(new_handover.declared_amount, Decimal('1000.00'))

    def test_cannot_submit_someone_elses_collection(self):
        self._collect(Decimal('500.00'), staff=self.other_staff)
        with self.assertRaises(InvalidHandoverPayment):
            submit_cash_handover(staff=self.staff, payments=selectors.unclaimed_cash_payments(self.other_staff), submitted_by=self.staff)

    def test_staff_cannot_confirm_own_handover(self):
        self._collect(Decimal('500.00'))
        handover = submit_cash_handover(staff=self.staff, payments=selectors.unclaimed_cash_payments(self.staff), submitted_by=self.staff)
        with self.assertRaises(SelfConfirmationNotAllowed):
            confirm_cash_handover(handover=handover, confirmed_amount=Decimal('500.00'), confirmed_by=self.staff)

    def test_cannot_confirm_a_non_submitted_handover_twice(self):
        self._collect(Decimal('500.00'))
        handover = submit_cash_handover(staff=self.staff, payments=selectors.unclaimed_cash_payments(self.staff), submitted_by=self.staff)
        confirm_cash_handover(handover=handover, confirmed_amount=Decimal('500.00'), confirmed_by=self.owner)
        with self.assertRaises(HandoverNotSubmitted):
            confirm_cash_handover(handover=handover, confirmed_amount=Decimal('500.00'), confirmed_by=self.owner)

    def test_cannot_hand_over_the_same_collection_twice(self):
        self._collect(Decimal('500.00'))
        submit_cash_handover(staff=self.staff, payments=selectors.unclaimed_cash_payments(self.staff), submitted_by=self.staff)
        with self.assertRaises(InvalidHandoverPayment):
            submit_cash_handover(staff=self.staff, payments=self.staff.collections.all(), submitted_by=self.staff)

    def test_confirmation_is_audited_with_discrepancy(self):
        self._collect(Decimal('8500.00'))
        handover = submit_cash_handover(staff=self.staff, payments=selectors.unclaimed_cash_payments(self.staff), submitted_by=self.staff)
        confirm_cash_handover(handover=handover, confirmed_amount=Decimal('8000.00'), confirmed_by=self.owner)
        entry = AuditLog.objects.get(action=Action.CASH_HANDOVER_CONFIRMED)
        self.assertEqual(entry.new_value['discrepancy'], '500.00')

class CashRBACTests(CashTestBase):

    def test_staff_cannot_see_another_staffs_cash(self):
        self._collect(Decimal('500.00'), staff=self.staff)
        self._collect(Decimal('700.00'), staff=self.other_staff, partition_suffix='2')
        handover_mine = submit_cash_handover(staff=self.staff, payments=selectors.unclaimed_cash_payments(self.staff), submitted_by=self.staff)
        submit_cash_handover(staff=self.other_staff, payments=selectors.unclaimed_cash_payments(self.other_staff), submitted_by=self.other_staff)
        visible = cash_handovers_visible_to(self.staff)
        self.assertQuerySetEqual(visible, [handover_mine], transform=lambda h: h)

    def test_business_manager_sees_handovers_from_staff_in_their_business(self):
        manager = User.objects.create_user('manager')
        assign_business_manager(user=manager, business=self.business, assigned_by=self.owner)
        self._collect(Decimal('500.00'), staff=self.staff)
        handover = submit_cash_handover(staff=self.staff, payments=selectors.unclaimed_cash_payments(self.staff), submitted_by=self.staff)
        visible = cash_handovers_visible_to(manager)
        self.assertQuerySetEqual(visible, [handover], transform=lambda h: h)

    def test_owner_sees_all_handovers(self):
        self._collect(Decimal('500.00'), staff=self.staff)
        self._collect(Decimal('700.00'), staff=self.other_staff, partition_suffix='2')
        submit_cash_handover(staff=self.staff, payments=selectors.unclaimed_cash_payments(self.staff), submitted_by=self.staff)
        submit_cash_handover(staff=self.other_staff, payments=selectors.unclaimed_cash_payments(self.other_staff), submitted_by=self.other_staff)
        self.assertEqual(cash_handovers_visible_to(self.owner).count(), 2)
