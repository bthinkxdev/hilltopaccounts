"""Cancelling / correcting an advance collection and voiding / correcting a partner payout.

Nothing is deleted: the row is kept, marked voided with who/when/why, and drops out of every total.
"""
from decimal import Decimal
from django.urls import reverse
from apps.audit.models import Action, AuditLog
from apps.billing.services import collect_rent
from apps.businesses.models import Partner, PartnerPayout, PartnerVillaShare
from apps.businesses.selectors import villa_period_payouts
from apps.businesses.services import correct_payout, void_payout
from apps.cash.services import confirm_cash_handover, submit_cash_handover
from apps.shared.exceptions import DomainError
from apps.tenancy.models import DepositCollection, Tenant
from apps.tenancy.services import collect_deposit, correct_deposit, void_deposit
from .tests_villa_stats import LAST_MONTH, RENT, THIS_MONTH, TODAY, ZERO, VillaStatsBase

ADVANCE = Decimal('1000.00')


class DepositVoidTests(VillaStatsBase):

    def setUp(self):
        super().setUp()
        self.tenant = Tenant.objects.get(partition=self.p1)
        update_advance = Tenant.objects.filter(pk=self.tenant.pk)
        update_advance.update(deposit=ADVANCE)
        self.tenant.refresh_from_db()

    def _collect(self, amount, by=None, method='cash'):
        return collect_deposit(tenant=self.tenant, amount=amount, method=method, collected_by=by or self.owner)

    def income(self):
        return self.page(LAST_MONTH)['pnl']['income']  # the advance belongs to the tenant's move-in month

    def test_void_removes_the_amount_from_received_income_and_reopens_pending(self):
        collection = self._collect(Decimal('600.00'))
        self.tenant.refresh_from_db()
        self.assertEqual((self.tenant.deposit_paid, self.tenant.deposit_pending), (Decimal('600.00'), Decimal('400.00')))
        void_deposit(collection=collection, voided_by=self.owner, reason='entered on the wrong tenant')
        self.tenant.refresh_from_db()
        self.assertEqual((self.tenant.deposit_paid, self.tenant.deposit_pending), (ZERO, ADVANCE))
        self.assertEqual(self.tenant.deposit_status, Tenant.DepositStatus.PENDING)
        self.assertEqual(DepositCollection.objects.filter(tenant=self.tenant).count(), 0)
        kept = DepositCollection.all_objects.get(pk=collection.pk)  # never deleted
        self.assertTrue(kept.is_voided)
        self.assertEqual((kept.voided_by, kept.void_reason), (self.owner, 'entered on the wrong tenant'))

    def test_void_drops_the_advance_out_of_income(self):
        self.login('owner')
        before = self.income()
        collection = self._collect(Decimal('600.00'))
        self.assertEqual(self.income(), before + Decimal('600.00'))
        void_deposit(collection=collection, voided_by=self.owner, reason='mistake')
        self.assertEqual(self.income(), before)

    def test_void_needs_a_reason_and_cannot_repeat(self):
        collection = self._collect(Decimal('100.00'))
        with self.assertRaises(DomainError):
            void_deposit(collection=collection, voided_by=self.owner, reason='  ')
        void_deposit(collection=collection, voided_by=self.owner, reason='dup')
        with self.assertRaises(DomainError):
            void_deposit(collection=collection, voided_by=self.owner, reason='again')
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.deposit_paid, ZERO)

    def test_void_is_audited_with_old_and_new_values(self):
        collection = self._collect(Decimal('250.00'))
        void_deposit(collection=collection, voided_by=self.owner, reason='typo')
        entry = AuditLog.objects.get(action=Action.DEPOSIT_COLLECTION_VOIDED)
        self.assertEqual((entry.user, entry.villa, entry.reason), (self.owner, self.villa, 'typo'))
        self.assertEqual(entry.old_value['amount'], '250.00')
        self.assertTrue(entry.new_value['voided'])

    def test_cash_in_a_live_handover_cannot_be_voided_until_rejected(self):
        collection = self._collect(Decimal('300.00'), by=self.staff)
        handover = submit_cash_handover(staff=self.staff, payments=[], deposits=[collection], submitted_by=self.staff)
        with self.assertRaises(DomainError):
            void_deposit(collection=collection, voided_by=self.owner, reason='x')
        confirm_cash_handover(handover=handover, confirmed_amount=Decimal('300.00'), confirmed_by=self.owner)
        with self.assertRaises(DomainError):
            void_deposit(collection=collection, voided_by=self.owner, reason='x')
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.deposit_paid, Decimal('300.00'))

    def test_voided_cash_no_longer_counts_as_staff_cash_to_hand_over(self):
        from apps.cash import selectors as cash_selectors
        collection = self._collect(Decimal('300.00'), by=self.staff)
        self.assertIn(collection, cash_selectors.unclaimed_cash_deposits(self.staff))
        void_deposit(collection=collection, voided_by=self.owner, reason='wrong amount')
        self.assertNotIn(collection, cash_selectors.unclaimed_cash_deposits(self.staff))

    def test_correct_replaces_the_amount_in_one_step(self):
        collection = self._collect(Decimal('900.00'))
        new = correct_deposit(collection=collection, new_amount=Decimal('600.00'), voided_by=self.owner, reason='counted wrong')
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.deposit_paid, Decimal('600.00'))
        self.assertEqual(list(DepositCollection.objects.filter(tenant=self.tenant)), [new])
        self.assertTrue(DepositCollection.all_objects.get(pk=collection.pk).is_voided)
        self.assertTrue(AuditLog.objects.filter(action=Action.DEPOSIT_COLLECTION_CORRECTED).exists())

    def test_correct_above_the_advance_rolls_everything_back(self):
        collection = self._collect(Decimal('900.00'))
        with self.assertRaises(DomainError):
            correct_deposit(collection=collection, new_amount=Decimal('1500.00'), voided_by=self.owner, reason='x')
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.deposit_paid, Decimal('900.00'))
        self.assertFalse(DepositCollection.all_objects.get(pk=collection.pk).is_voided)

    def test_void_view_needs_a_reason_and_is_blocked_for_other_roles(self):
        collection = self._collect(Decimal('200.00'))
        url = reverse('tenancy:deposit_void', args=[collection.pk])
        self.login('owner')
        self.client.post(url, {'reason': ''})
        self.assertFalse(DepositCollection.all_objects.get(pk=collection.pk).is_voided)
        self.assertEqual(self.client.post(url, {'reason': 'duplicate'}).status_code, 302)
        self.assertTrue(DepositCollection.all_objects.get(pk=collection.pk).is_voided)
        outsider = DepositCollection.all_objects.create(tenant=self.tenant, amount=Decimal('50.00'), collected_at=TODAY, collected_by=self.owner)
        from apps.accounts.models import User
        User.objects.create_user('stranger', password='Passw0rd!2026')
        self.login('stranger')
        self.assertIn(self.client.post(reverse('tenancy:deposit_void', args=[outsider.pk]), {'reason': 'x'}).status_code, (403, 404))
        self.assertFalse(DepositCollection.all_objects.get(pk=outsider.pk).is_voided)


class PartnerPayoutVoidTests(VillaStatsBase):

    def setUp(self):
        super().setUp()
        self.partner = Partner.objects.create(name='P', created_by=self.owner)
        PartnerVillaShare.objects.create(partner=self.partner, villa=self.villa, percentage=Decimal('50.00'))
        self.payout = PartnerPayout.objects.create(partner=self.partner, villa=self.villa, period_year=LAST_MONTH.year, period_month=LAST_MONTH.month, amount=Decimal('800.00'), paid_on=TODAY, created_by=self.owner)

    def paid(self):
        return sum((p.amount for p in villa_period_payouts(self.villa, LAST_MONTH.year, LAST_MONTH.month)), ZERO)

    def test_void_keeps_the_row_and_stops_counting_it_as_paid(self):
        self.assertEqual(self.paid(), Decimal('800.00'))
        void_payout(payout=self.payout, voided_by=self.owner, reason='bank transfer bounced')
        self.assertEqual(self.paid(), ZERO)
        self.assertEqual(self.partner.payouts.count(), 0)
        kept = PartnerPayout.all_objects.get(pk=self.payout.pk)
        self.assertEqual((kept.is_voided, kept.voided_by, kept.void_reason), (True, self.owner, 'bank transfer bounced'))

    def test_void_needs_a_reason_and_cannot_repeat(self):
        with self.assertRaises(DomainError):
            void_payout(payout=self.payout, voided_by=self.owner, reason='')
        void_payout(payout=self.payout, voided_by=self.owner, reason='wrong partner')
        with self.assertRaises(DomainError):
            void_payout(payout=self.payout, voided_by=self.owner, reason='again')

    def test_void_is_audited(self):
        void_payout(payout=self.payout, voided_by=self.owner, reason='bounced')
        entry = AuditLog.objects.get(action=Action.PARTNER_PAYOUT_VOIDED)
        self.assertEqual((entry.user, entry.villa, entry.reason), (self.owner, self.villa, 'bounced'))
        self.assertEqual(entry.old_value['amount'], '800.00')

    def test_correct_voids_the_original_and_records_the_replacement(self):
        new = correct_payout(payout=self.payout, voided_by=self.owner, reason='typed 800 not 600', amount=Decimal('600.00'))
        self.assertEqual(self.paid(), Decimal('600.00'))
        self.assertEqual((new.partner, new.villa, new.period_year, new.period_month), (self.partner, self.villa, LAST_MONTH.year, LAST_MONTH.month))
        self.assertTrue(PartnerPayout.all_objects.get(pk=self.payout.pk).is_voided)
        self.assertTrue(AuditLog.objects.filter(action=Action.PARTNER_PAYOUT_CORRECTED).exists())

    def test_correct_with_a_bad_amount_changes_nothing(self):
        with self.assertRaises(DomainError):
            correct_payout(payout=self.payout, voided_by=self.owner, reason='x', amount=ZERO)
        self.assertFalse(PartnerPayout.all_objects.get(pk=self.payout.pk).is_voided)

    def test_only_the_owner_can_void_and_the_old_delete_url_is_gone(self):
        url = reverse('businesses:partner_payout_void', args=[self.payout.pk])
        self.login('manager')
        self.assertEqual(self.client.post(url, {'reason': 'x'}).status_code, 403)
        self.login('owner')
        self.assertEqual(self.client.post(url, {'reason': 'wrong partner'}).status_code, 302)
        self.assertTrue(PartnerPayout.all_objects.get(pk=self.payout.pk).is_voided)
        self.assertFalse(hasattr(__import__('apps.businesses.views', fromlist=['x']), 'partner_payout_delete'))

    def test_partner_page_still_lists_the_voided_payout_as_history(self):
        void_payout(payout=self.payout, voided_by=self.owner, reason='bounced')
        self.login('owner')
        page = self.client.get(reverse('businesses:partner_detail', args=[self.partner.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Voided')
        self.assertContains(page, 'bounced')
