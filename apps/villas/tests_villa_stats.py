"""Villa statistics under the month-isolation rule.

Income belongs to the month the rent is FOR, never to the day it was collected; expenses to their own month.
The collection date is shown on the collection record only. Rent and advance are fixed at onboarding;
collections can be partial and the rest stays pending.
"""
from datetime import date, timedelta
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User
from apps.accounts.services import assign_business_manager, assign_villa_staff
from apps.billing.models import Charge, Invoice, Payment
from apps.billing.services import collect_rent
from apps.businesses.models import Partner, PartnerPayout, PartnerVillaShare
from apps.businesses.selectors import partner_shares_due_after_payout, villa_period_payouts
from apps.businesses.services import create_business
from apps.cash import selectors as cash_selectors
from apps.cash.services import confirm_cash_handover, submit_cash_handover
from apps.shared.exceptions import DomainError
from apps.tenancy.models import Tenant
from apps.tenancy.services import collect_deposit, create_tenant, move_out_tenant, update_tenant
from apps.villas.services import create_partition, create_villa

PASSWORD = 'Passw0rd!2026'
TODAY = date.today()
THIS_MONTH = TODAY.replace(day=1)
LAST_MONTH = (THIS_MONTH - timedelta(days=1)).replace(day=1)
NEXT_MONTH = (THIS_MONTH + timedelta(days=32)).replace(day=1)
RENT = Decimal('2300.00')
ZERO = Decimal('0.00')


class VillaStatsBase(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', password=PASSWORD, is_owner=True)
        self.manager = User.objects.create_user('manager', password=PASSWORD)
        self.staff = User.objects.create_user('staff', password=PASSWORD)
        self.business = create_business(name='Biz', created_by=self.owner)
        self.villa = create_villa(business=self.business, name='Villa', created_by=self.owner)
        assign_business_manager(user=self.manager, business=self.business, assigned_by=self.owner)
        assign_villa_staff(user=self.staff, villa=self.villa, assigned_by=self.owner)
        self.p1 = self.occupied('101', move_in=LAST_MONTH)
        self.p2 = self.occupied('GF-02', move_in=LAST_MONTH)

    def occupied(self, name, move_in, rent=RENT, **tenant_fields):
        partition = create_partition(villa=self.villa, name=name, created_by=self.owner)
        create_tenant(partition=partition, name=f'T {name}', move_in_date=move_in, monthly_rent=rent, created_by=self.owner, **tenant_fields)
        return partition

    def login(self, username):
        self.client.logout()
        self.assertTrue(self.client.login(username=username, password=PASSWORD))

    def collect(self, partition, month_start, collected_at=None, amount=None):
        data = {'year': month_start.year, 'month': month_start.month, 'method': 'cash', 'next': '/'}
        if collected_at:
            data['collected_at'] = collected_at.isoformat()
        if amount is not None:
            data['amount'] = str(amount)
        return self.client.post(reverse('billing:rent_collect', args=[partition.pk]), data)

    def page(self, month_start):
        return self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), {'period': 'month', 'year': month_start.year, 'month': month_start.month}).context


class MonthIsolationTests(VillaStatsBase):

    def test_arrears_and_advance_are_income_of_their_own_month(self):
        self.login('owner')
        self.collect(self.p1, THIS_MONTH)
        self.collect(self.p2, LAST_MONTH)  # last month's rent, collected today
        self.collect(self.p2, NEXT_MONTH)  # next month's rent, collected today
        self.assertEqual(self.page(THIS_MONTH)['pnl']['income'], RENT)
        self.assertEqual(self.page(LAST_MONTH)['pnl']['income'], RENT)
        self.assertEqual(self.page(NEXT_MONTH)['pnl']['income'], RENT)

    def test_rent_card_reconciles_with_income(self):
        self.login('staff')
        self.collect(self.p1, THIS_MONTH, amount=Decimal('1000.00'))
        self.login('owner')
        self.collect(self.p2, THIS_MONTH)
        self.collect(self.p2, LAST_MONTH)
        ctx = self.page(THIS_MONTH)
        rent, pnl = ctx['rent_totals'], ctx['pnl']
        self.assertEqual((rent['expected'], rent['collected'], rent['pending']), (RENT * 2, RENT + Decimal('1000.00'), RENT - Decimal('1000.00')))
        self.assertEqual(rent['expected'], rent['collected'] + rent['pending'])
        self.assertEqual((rent['staff_collected'], rent['admin_collected']), (Decimal('1000.00'), RENT))
        self.assertEqual((pnl['income'], pnl['staff_collected'], pnl['admin_collected']), (rent['collected'], rent['staff_collected'], rent['admin_collected']))

    def test_income_link_lists_exactly_that_months_rent_with_collection_dates(self):
        self.login('owner')
        self.collect(self.p1, THIS_MONTH)
        self.collect(self.p2, LAST_MONTH)  # collected today, but it is last month's income
        ctx = self.page(THIS_MONTH)
        r = self.client.get(ctx['period_pnl_urls']['income'])
        self.assertEqual((r.context['tab'], r.context['received_total']), ('collected', ctx['pnl']['income']))
        self.assertEqual([row['partition'].pk for row in r.context['received_payments']], [self.p1.pk])
        self.assertContains(r, TODAY.strftime('%d %b %Y'))
        # Last month's rent, although collected today, is listed under last month.
        last = self.client.get(reverse('billing:payment_list'), {'tab': 'collected', 'year': LAST_MONTH.year, 'month': LAST_MONTH.month})
        self.assertEqual([row['partition'].pk for row in last.context['received_payments']], [self.p2.pk])
        self.assertContains(last, TODAY.strftime('%d %b %Y'))
        # The removed tabs fall back to the default instead of erroring.
        self.assertEqual(self.client.get(reverse('billing:payment_list'), {'tab': 'received'}).context['tab'], 'all')

    def test_collector_links_match_their_cards(self):
        self.login('staff')
        self.collect(self.p1, THIS_MONTH)
        self.login('owner')
        self.collect(self.p2, THIS_MONTH)
        ctx = self.page(THIS_MONTH)
        self.assertEqual(self.client.get(ctx['period_pnl_urls']['staff_collections']).context['received_total'], ctx['pnl']['staff_collected'])
        self.assertEqual(self.client.get(ctx['period_pnl_urls']['admin_collections']).context['received_total'], ctx['pnl']['admin_collected'])

    def test_rent_rows_show_when_and_by_whom_rent_was_collected(self):
        self.login('owner')
        self.collect(self.p1, THIS_MONTH, collected_at=THIS_MONTH)
        r = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), {'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month})
        self.assertContains(r, f"Collected QAR 2,300.00 on {THIS_MONTH.strftime('%d %b %Y')} by Admin (Cash)")
        self.assertNotContains(r, 'Admin (Admin')

    def test_collected_list_shows_collection_time_for_same_day_entries(self):
        from django.utils import timezone
        self.login('owner')
        self.collect(self.p1, LAST_MONTH)  # collected today, counted in last month
        r = self.client.get(reverse('billing:payment_list'), {'tab': 'collected', 'year': LAST_MONTH.year, 'month': LAST_MONTH.month})
        recorded = timezone.localtime(Payment.objects.get().created_at)
        self.assertContains(r, recorded.strftime('%I:%M %p').lstrip('0'))
        self.assertContains(r, f"Collected for {LAST_MONTH.strftime('%B %Y')}")

    def test_yearly_income_link_keeps_the_year(self):
        self.login('owner')
        self.collect(self.p1, THIS_MONTH)
        ctx = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), {'period': 'year', 'year': TODAY.year}).context
        r = self.client.get(ctx['period_pnl_urls']['income'])
        self.assertEqual((r.context['period'], r.context['received_total']), ('year', ctx['pnl']['income']))

    def test_entered_collection_date_is_stored_and_future_is_rejected(self):
        self.login('owner')
        self.collect(self.p1, THIS_MONTH, collected_at=THIS_MONTH)
        self.assertEqual(Payment.objects.get().collected_at, THIS_MONTH)
        with self.assertRaises(DomainError):
            collect_rent(partition=self.p2, year=THIS_MONTH.year, month=THIS_MONTH.month, method='cash', collected_by=self.owner, collected_at=TODAY + timedelta(days=1))


class FixedRentTests(VillaStatsBase):

    def test_collecting_never_changes_the_rent_and_partial_leaves_pending(self):
        self.login('owner')
        r = self.client.post(reverse('billing:rent_collect', args=[self.p1.pk]), {'year': THIS_MONTH.year, 'month': THIS_MONTH.month, 'method': 'cash', 'amount': '1000.00', 'rent_amount': '999.00', 'next': '/'})
        self.assertEqual(r.status_code, 302)
        tenant = Tenant.objects.get(partition=self.p1, status=Tenant.Status.ACTIVE)
        self.assertEqual(tenant.monthly_rent, RENT)
        row = [r for r in self.page(THIS_MONTH)['rent_rows'] if r['partition'].pk == self.p1.pk][0]
        self.assertEqual((row['due'], row['paid'], row['balance'], row['status']), (RENT, Decimal('1000.00'), RENT - Decimal('1000.00'), 'partial'))

    def test_a_charge_added_after_the_month_was_billed_can_be_collected_in_one_go(self):
        from apps.billing.services import create_charge, get_or_create_charge_type
        p = self.occupied('room2', move_in=THIS_MONTH, rent=Decimal('1200.00'))
        self.login('owner')
        self.page(THIS_MONTH)  # the month is billed: Rent 1,200
        self.collect(p, THIS_MONTH, amount=Decimal('200.00'))
        create_charge(partition=p, charge_type=get_or_create_charge_type(name='Gas'), amount=Decimal('500.00'), start_date=THIS_MONTH, created_by=self.owner, frequency=Charge.Frequency.MONTHLY)
        row = [r for r in self.page(THIS_MONTH)['rent_rows'] if r['partition'].pk == p.pk][0]
        self.assertEqual((row['due'], row['paid'], row['balance']), (Decimal('1700.00'), Decimal('200.00'), Decimal('1500.00')))
        r = self.collect(p, THIS_MONTH, amount=Decimal('1500.00'))
        self.assertEqual(r.status_code, 302)
        row = [r for r in self.page(THIS_MONTH)['rent_rows'] if r['partition'].pk == p.pk][0]
        self.assertEqual((row['paid'], row['balance'], row['status']), (Decimal('1700.00'), ZERO, 'collected'))
        self.assertEqual(sorted(Payment.objects.filter(invoice__partition=p).values_list('amount', flat=True)), [Decimal('200.00'), Decimal('500.00'), Decimal('1000.00')])
        with self.assertRaises(DomainError):  # nothing left to collect
            collect_rent(partition=p, year=THIS_MONTH.year, month=THIS_MONTH.month, method='cash', collected_by=self.owner)

    def test_a_rent_change_applies_from_the_first_unbilled_month_only(self):
        self.login('owner')
        self.page(THIS_MONTH)  # this month gets billed at the current rent
        tenant = Tenant.objects.get(partition=self.p1, status=Tenant.Status.ACTIVE)
        update_tenant(tenant=tenant, updated_by=self.owner, monthly_rent=Decimal('2500.00'))
        self.assertEqual(tenant.rent_effective_from, NEXT_MONTH)
        this_row = [r for r in self.page(THIS_MONTH)['rent_rows'] if r['partition'].pk == self.p1.pk][0]
        next_row = [r for r in self.page(NEXT_MONTH)['rent_rows'] if r['partition'].pk == self.p1.pk][0]
        self.assertEqual((this_row['due'], next_row['due']), (RENT, Decimal('2500.00')))
        self.assertEqual(Charge.objects.filter(partition=self.p1, is_active=True, charge_type__name='Rent').count(), 2)


class AdvanceTests(VillaStatsBase):

    def test_advance_is_fixed_collected_in_parts_with_dates_and_is_income_of_the_collection_month(self):
        p = self.occupied('ADV', move_in=THIS_MONTH, deposit=Decimal('3000.00'), deposit_paid=Decimal('1000.00'))
        tenant = Tenant.objects.get(partition=p)
        self.assertEqual((tenant.deposit_paid, tenant.deposit_pending, tenant.deposit_status), (Decimal('1000.00'), Decimal('2000.00'), Tenant.DepositStatus.PARTIALLY_PAID))
        collect_deposit(tenant=tenant, amount=Decimal('1500.00'), method='cash', collected_by=self.staff)
        self.assertEqual((tenant.deposit_paid, tenant.deposit_pending), (Decimal('2500.00'), Decimal('500.00')))
        with self.assertRaises(DomainError):
            collect_deposit(tenant=tenant, amount=Decimal('600.00'), method='cash', collected_by=self.staff)
        self.assertEqual([c.amount for c in tenant.deposit_collections.order_by('id')], [Decimal('1000.00'), Decimal('1500.00')])
        self.login('owner')
        pnl = self.page(THIS_MONTH)['pnl']
        self.assertEqual((pnl['income'], pnl['rent_income'], pnl['advance_income']), (Decimal('2500.00'), ZERO, Decimal('2500.00')))
        self.assertEqual((pnl['staff_collected'], pnl['admin_collected']), (Decimal('1500.00'), Decimal('1000.00')))
        self.assertEqual(self.page(NEXT_MONTH)['pnl']['income'], ZERO)  # pending advance is income only once collected
        tile = [t for t in self.page(THIS_MONTH)['partitions'] if t.pk == p.pk][0]
        self.assertEqual(tile.pnl['income'], Decimal('2500.00'))

    def test_income_list_includes_advance_and_matches_the_card(self):
        p = self.occupied('ADV', move_in=THIS_MONTH, deposit=Decimal('3000.00'), deposit_paid=Decimal('1000.00'))
        self.login('owner')
        self.collect(self.p1, THIS_MONTH)
        ctx = self.page(THIS_MONTH)
        r = self.client.get(ctx['period_pnl_urls']['income'])
        self.assertEqual(r.context['received_total'], ctx['pnl']['income'])
        self.assertEqual(ctx['pnl']['income'], RENT + Decimal('1000.00'))
        self.assertContains(r, 'Advance / deposit')
        self.assertEqual(sorted(row['for_label'] for row in r.context['received_payments']), sorted(['Advance / deposit', THIS_MONTH.strftime('%B %Y')]))
        self.assertTrue(any(row['partition'].pk == p.pk for row in r.context['received_payments']))

    def test_staff_advance_cash_is_handed_over_like_rent(self):
        p = self.occupied('ADV', move_in=THIS_MONTH, deposit=Decimal('3000.00'))
        tenant = Tenant.objects.get(partition=p)
        advance = collect_deposit(tenant=tenant, amount=Decimal('3000.00'), method='cash', collected_by=self.staff)
        [rent] = collect_rent(partition=self.p1, year=THIS_MONTH.year, month=THIS_MONTH.month, method='cash', collected_by=self.staff)
        self.assertEqual(cash_selectors.outstanding_cash_for(self.staff), RENT + Decimal('3000.00'))
        cash = cash_selectors.villa_cash_position(self.villa)
        self.assertEqual((cash['collected'], cash['holding']), (RENT + Decimal('3000.00'), RENT + Decimal('3000.00')))
        self.assertEqual(cash_selectors.unhanded_staff_cash_for_month(self.villa, THIS_MONTH.year, THIS_MONTH.month), RENT + Decimal('3000.00'))
        self.login('staff')
        self.client.post(reverse('cash:handover_submit'), {'payments': [rent.pk], 'deposits': [advance.pk]})
        handover = self.staff.cash_handovers.get()
        self.assertEqual((handover.declared_amount, list(handover.deposits.all())), (RENT + Decimal('3000.00'), [advance]))
        confirm_cash_handover(handover=handover, confirmed_amount=handover.declared_amount, confirmed_by=self.owner)
        cash = cash_selectors.villa_cash_position(self.villa)
        self.assertEqual((cash['received_by_owner'], cash['holding']), (RENT + Decimal('3000.00'), ZERO))
        self.assertEqual(cash_selectors.outstanding_cash_for(self.staff), ZERO)
        self.assertEqual(cash_selectors.unhanded_staff_cash_for_month(self.villa, THIS_MONTH.year, THIS_MONTH.month), ZERO)
        self.login('owner')
        self.assertEqual(self.client.get(reverse('cash:handover_detail', args=[handover.pk])).status_code, 200)

    def test_advance_cannot_drop_below_what_was_collected(self):
        p = self.occupied('ADV', move_in=THIS_MONTH, deposit=Decimal('3000.00'), deposit_paid=Decimal('1000.00'))
        with self.assertRaises(DomainError):
            update_tenant(tenant=Tenant.objects.get(partition=p), updated_by=self.owner, deposit=Decimal('500.00'))

    def test_no_advance_is_its_own_status(self):
        tenant = Tenant.objects.get(partition=self.p1)
        self.assertEqual((tenant.deposit, tenant.deposit_status), (ZERO, Tenant.DepositStatus.NONE))

    def test_collect_advance_form(self):
        p = self.occupied('ADV', move_in=THIS_MONTH, deposit=Decimal('3000.00'))
        tenant = Tenant.objects.get(partition=p)
        self.login('staff')
        self.client.post(reverse('tenancy:deposit_edit', args=[tenant.pk]), {'deposit': '3000.00', 'amount': '1200.00', 'method': 'bank_transfer', 'collected_at': TODAY.isoformat(), 'next': 'https://evil.example/'})
        tenant.refresh_from_db()
        self.assertEqual((tenant.deposit_paid, tenant.deposit_collections.get().method), (Decimal('1200.00'), 'bank_transfer'))


class EveryMonthBilledTests(VillaStatsBase):

    def test_untouched_months_are_billed_and_count_as_outstanding(self):
        self.assertFalse(Invoice.objects.exists())
        self.login('owner')
        self.client.get(reverse('dashboard:home'))
        self.assertEqual(Invoice.objects.filter(billing_period_start=THIS_MONTH).count(), 2)
        self.assertFalse(Invoice.objects.filter(billing_period_start=LAST_MONTH).exists())  # tracking starts when added
        self.assertEqual(self.client.get(reverse('dashboard:home')).context['outstanding'], RENT * 2)

    def test_moved_out_tenant_stays_on_the_rent_sheet_for_that_month(self):
        tenant = Tenant.objects.get(partition=self.p1)
        move_out_tenant(tenant=tenant, move_out_date=TODAY, moved_out_by=self.owner)
        self.login('owner')
        rows = self.page(THIS_MONTH)['rent_rows']
        moved = [r for r in rows if r['tenant'].pk == tenant.pk][0]
        self.assertEqual((moved['is_current'], moved['due'], moved['paid']), (False, RENT, ZERO))
        self.assertEqual(self.page(THIS_MONTH)['rent_totals']['expected'], RENT * 2)


class PartnerPayoutTests(VillaStatsBase):

    def setUp(self):
        super().setUp()
        self.other_villa = create_villa(business=self.business, name='Other', created_by=self.owner)
        self.partner = Partner.objects.create(name='P', created_by=self.owner)
        PartnerVillaShare.objects.create(partner=self.partner, villa=self.villa, percentage=Decimal('50.00'))

    def test_payout_without_villa_counts_only_for_a_single_villa_partner(self):
        PartnerPayout.objects.create(partner=self.partner, villa=None, period_year=THIS_MONTH.year, period_month=THIS_MONTH.month, amount=Decimal('100.00'), paid_on=TODAY, created_by=self.owner)
        self.assertEqual(len(villa_period_payouts(self.villa, THIS_MONTH.year, THIS_MONTH.month)), 1)
        PartnerVillaShare.objects.create(partner=self.partner, villa=self.other_villa, percentage=Decimal('50.00'))
        self.assertEqual(villa_period_payouts(self.villa, THIS_MONTH.year, THIS_MONTH.month), [])

    def test_only_that_months_staff_cash_blocks_its_payout(self):
        [payment] = collect_rent(partition=self.p1, year=LAST_MONTH.year, month=LAST_MONTH.month, method='cash', collected_by=self.staff)
        self.assertEqual(cash_selectors.unhanded_staff_cash_for_month(self.villa, THIS_MONTH.year, THIS_MONTH.month), ZERO)
        self.assertEqual(cash_selectors.unhanded_staff_cash_for_month(self.villa, LAST_MONTH.year, LAST_MONTH.month), RENT)
        confirm_cash_handover(handover=submit_cash_handover(staff=self.staff, payments=[payment], submitted_by=self.staff), confirmed_amount=RENT, confirmed_by=self.owner)
        self.assertEqual(cash_selectors.unhanded_staff_cash_for_month(self.villa, LAST_MONTH.year, LAST_MONTH.month), ZERO)

    def test_alert_when_a_paid_month_later_earns_more(self):
        collect_rent(partition=self.p1, year=LAST_MONTH.year, month=LAST_MONTH.month, method='bank_transfer', collected_by=self.owner)
        PartnerPayout.objects.create(partner=self.partner, villa=self.villa, period_year=LAST_MONTH.year, period_month=LAST_MONTH.month, amount=RENT / 2, paid_on=TODAY, created_by=self.owner)
        self.assertEqual(partner_shares_due_after_payout(), [])
        collect_rent(partition=self.p2, year=LAST_MONTH.year, month=LAST_MONTH.month, method='bank_transfer', collected_by=self.owner)  # arrears arrive later
        due = partner_shares_due_after_payout()
        self.assertEqual([(d['villa'], d['remaining']) for d in due], [(self.villa, RENT / 2)])


class ProfitSplitTests(VillaStatsBase):
    """Everything goes by the shares: each villa's profit OR loss is split by its partners' %, the owner keeps the
    rest, and owner + partners always equals the net. Partners only share in their own villas."""

    def setUp(self):
        super().setUp()
        from apps.expenses.services import create_expense, get_or_create_category
        self.expense = lambda villa, amount, day: create_expense(villa=villa, category=get_or_create_category(name=f'E{amount}{day}'), amount=Decimal(amount), date=day, created_by=self.owner)
        self.partner = Partner.objects.create(name='Partner A', created_by=self.owner)
        self.share = PartnerVillaShare.objects.create(partner=self.partner, villa=self.villa, percentage=Decimal('50.00'))
        self.login('owner')

    def test_a_loss_in_another_villa_is_carried_by_the_owner(self):
        collect_rent(partition=self.p1, year=THIS_MONTH.year, month=THIS_MONTH.month, method='bank_transfer', collected_by=self.owner)  # villa +2300
        other = create_villa(business=self.business, name='Other', created_by=self.owner)
        self.expense(other, '3000.00', THIS_MONTH)  # other villa −3000
        ctx = self.client.get(reverse('dashboard:home'), {'year': THIS_MONTH.year, 'month': THIS_MONTH.month}).context
        self.assertEqual((ctx['distributable_profit'], ctx['total_partner_cut'], ctx['owner_share_amount']), (Decimal('-700.00'), Decimal('1150.00'), Decimal('-1850.00')))
        self.assertEqual(ctx['owner_share_amount'] + ctx['total_partner_cut'], ctx['distributable_profit'])
        self.assertEqual([c['cut'] for c in ctx['partner_share_cards']], [Decimal('1150.00')])  # still shown although the total is a loss
        page = self.client.get(reverse('businesses:partner_list'), {'year': THIS_MONTH.year, 'month': THIS_MONTH.month}).context
        self.assertEqual((page['total_net'], page['total_owner_cut'], page['total_partner_payout']), (Decimal('-700.00'), Decimal('-1850.00'), Decimal('1150.00')))
        self.assertEqual(sorted(b['cut'] for b in page['owner_villa_breakdown']), [Decimal('-3000.00'), Decimal('1150.00')])

    def test_partner_in_villas_a_and_b_is_not_touched_by_a_loss_in_villa_c(self):
        villa_b = create_villa(business=self.business, name='Villa B', created_by=self.owner)
        villa_c = create_villa(business=self.business, name='Villa C', created_by=self.owner)
        PartnerVillaShare.objects.create(partner=self.partner, villa=villa_b, percentage=Decimal('50.00'))
        other_partner = Partner.objects.create(name='Partner C', created_by=self.owner)
        PartnerVillaShare.objects.create(partner=other_partner, villa=villa_c, percentage=Decimal('40.00'))
        collect_rent(partition=self.p1, year=THIS_MONTH.year, month=THIS_MONTH.month, method='bank_transfer', collected_by=self.owner)  # villa A +2300
        b_unit = create_partition(villa=villa_b, name='B1', created_by=self.owner)
        create_tenant(partition=b_unit, name='TB', move_in_date=THIS_MONTH, monthly_rent=Decimal('1000.00'), created_by=self.owner)
        collect_rent(partition=b_unit, year=THIS_MONTH.year, month=THIS_MONTH.month, method='bank_transfer', collected_by=self.owner)  # villa B +1000
        self.expense(villa_c, '5000.00', THIS_MONTH)  # villa C −5000
        detail = self.client.get(reverse('businesses:partner_detail', args=[self.partner.pk]), {'year': THIS_MONTH.year, 'month': THIS_MONTH.month}).context
        self.assertEqual({s.villa.name: s.cut for s in detail['shares']}, {'Villa': Decimal('1150.00'), 'Villa B': Decimal('500.00')})
        ctx = self.client.get(reverse('dashboard:home'), {'year': THIS_MONTH.year, 'month': THIS_MONTH.month}).context
        cuts = {c['name']: c['cut'] for c in ctx['partner_share_cards']}
        self.assertEqual(cuts, {'Partner A': Decimal('1650.00'), 'Partner C': Decimal('-2000.00')})  # C carries 40% of villa C's loss
        self.assertEqual(ctx['owner_share_amount'], Decimal('-1700.00') - (Decimal('1650.00') - Decimal('2000.00')))
        self.assertEqual(ctx['owner_share_amount'] + ctx['total_partner_cut'], ctx['distributable_profit'])

    def test_a_loss_month_is_shared_and_offset_in_the_partner_account(self):
        if LAST_MONTH.year != THIS_MONTH.year:
            self.skipTest('needs two months in the same year')
        self.expense(self.villa, '1300.00', LAST_MONTH)  # last month −1300
        last = self.page(LAST_MONTH)
        self.assertEqual((last['total_partner_cut'], last['owner_share_amount']), (Decimal('-650.00'), Decimal('-650.00')))
        self.assertEqual(last['villa_partner_shares'][0].cut, Decimal('-650.00'))
        collect_rent(partition=self.p1, year=THIS_MONTH.year, month=THIS_MONTH.month, method='bank_transfer', collected_by=self.owner)  # this month +2300
        year = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), {'period': 'year', 'year': THIS_MONTH.year}).context
        self.assertEqual((year['pnl']['net'], year['total_partner_cut'], year['owner_share_amount']), (Decimal('1000.00'), Decimal('500.00'), Decimal('500.00')))
        # The partner is paid this month's 1,150; their account shows last month's loss share offset against it.
        PartnerPayout.objects.create(partner=self.partner, villa=self.villa, period_year=THIS_MONTH.year, period_month=THIS_MONTH.month, amount=Decimal('1150.00'), paid_on=TODAY, created_by=self.owner)
        account = self.client.get(reverse('businesses:partner_detail', args=[self.partner.pk]), {'year': THIS_MONTH.year, 'month': THIS_MONTH.month}).context['account']
        self.assertEqual((account['earned'], account['paid'], account['balance']), (Decimal('500.00'), Decimal('1150.00'), Decimal('-650.00')))

    def test_shares_add_up_exactly_with_rounding(self):
        self.share.delete()
        for i in range(3):
            PartnerVillaShare.objects.create(partner=Partner.objects.create(name=f'P{i}', created_by=self.owner), villa=self.villa, percentage=Decimal('33.33'))
        self.collect(self.p1, THIS_MONTH, amount=Decimal('100.00'))
        ctx = self.page(THIS_MONTH)
        self.assertEqual(sum((s.cut for s in ctx['villa_partner_shares']), Decimal('0.00')) + ctx['owner_share_amount'], ctx['pnl']['net'])

    def test_partner_list_counts_only_payouts_for_the_villas_shown(self):
        other = create_villa(business=self.business, name='Other', created_by=self.owner)
        PartnerVillaShare.objects.create(partner=self.partner, villa=other, percentage=Decimal('50.00'))
        PartnerPayout.objects.create(partner=self.partner, villa=other, period_year=THIS_MONTH.year, period_month=THIS_MONTH.month, amount=Decimal('999.00'), paid_on=TODAY, created_by=self.owner)
        self.collect(self.p1, THIS_MONTH)
        page = self.client.get(reverse('businesses:partner_list'), {'year': THIS_MONTH.year, 'month': THIS_MONTH.month, 'villa': self.villa.pk}).context
        row = [p for p in page['page_obj'] if p.pk == self.partner.pk][0]
        self.assertEqual((row.total_payout, row.paid_amount, row.payment_status), (Decimal('1150.00'), Decimal('0.00'), 'pending'))

    def test_partner_detail_matches_villa_page(self):
        self.collect(self.p1, THIS_MONTH)
        villa_ctx = self.page(THIS_MONTH)
        detail = self.client.get(reverse('businesses:partner_detail', args=[self.partner.pk]), {'year': THIS_MONTH.year, 'month': THIS_MONTH.month}).context
        self.assertEqual((detail['shares'][0].cut, detail['shares'][0].owner_cut), (villa_ctx['villa_partner_shares'][0].cut, villa_ctx['owner_share_amount']))


class CollectionsPageTests(VillaStatsBase):

    def list_page(self, **params):
        return self.client.get(reverse('billing:payment_list'), {'year': THIS_MONTH.year, 'month': THIS_MONTH.month, **params})

    def test_earlier_unpaid_months_are_listed_and_collectable_from_the_page(self):
        self.login('owner')
        self.collect(self.p1, LAST_MONTH, amount=Decimal('300.00'))  # last month: 300 of 2300 paid
        r = self.list_page(tab='pending')
        arrears = r.context['arrears_rows']
        self.assertEqual([(a['partition'].pk, a['month'], a['balance']) for a in arrears], [(self.p1.pk, LAST_MONTH, Decimal('2000.00'))])
        self.assertEqual(r.context['portfolio_arrears'], Decimal('2000.00'))
        self.assertContains(r, f'data-year="{LAST_MONTH.year}" data-month="{LAST_MONTH.month}"')
        self.assertContains(r, f"Rent for <b>{LAST_MONTH.strftime('%B %Y')}</b>")
        # Collecting it from the list settles last month, not this month.
        self.client.post(reverse('billing:rent_collect', args=[self.p1.pk]), {'year': LAST_MONTH.year, 'month': LAST_MONTH.month, 'method': 'cash', 'amount': '2000.00', 'next': '/'})
        self.assertEqual(self.list_page(tab='pending').context['arrears_rows'], [])
        self.assertEqual(self.page(LAST_MONTH)['pnl']['rent_income'], RENT)
        self.assertEqual(self.page(THIS_MONTH)['pnl']['rent_income'], ZERO)

    def test_moved_out_tenants_old_balance_is_listed_with_record_payment(self):
        self.login('owner')
        self.page(THIS_MONTH)
        tenant = Tenant.objects.get(partition=self.p1)
        move_out_tenant(tenant=tenant, move_out_date=TODAY, moved_out_by=self.owner)
        r = self.list_page(year=NEXT_MONTH.year, month=NEXT_MONTH.month, tab='pending')
        row = [a for a in r.context['arrears_rows'] if a['tenant'].pk == tenant.pk][0]
        self.assertEqual((row['is_current'], row['balance']), (False, RENT))
        self.assertContains(r, reverse('billing:payment_record', args=[row['invoice'].pk]))

    def test_only_people_who_can_collect_see_collect_buttons(self):
        from apps.accounts.services import assign_accountant
        accountant = User.objects.create_user('accountant', password=PASSWORD)
        assign_accountant(user=accountant, business=self.business, assigned_by=self.owner)
        self.login('accountant')
        r = self.list_page()
        self.assertTrue(r.context['collection_rows'])
        self.assertFalse(any(row['can_manage'] for row in r.context['collection_rows']))
        self.assertNotContains(r, 'openRentCollectModal(this)">Collect')
        self.login('staff')
        self.assertTrue(all(row['can_manage'] for row in self.list_page().context['collection_rows']))

    def test_yearly_view_points_to_the_collected_lists(self):
        self.login('owner')
        r = self.list_page(period='year')
        self.assertEqual(r.context['collection_rows'], [])
        self.assertContains(r, 'Rent is collected month by month')


class ExpenseAndStaffTests(VillaStatsBase):
    """No 'paid by' choice anywhere: the payer is whoever records the payment (owner side for Owner / Business
    Manager, staff side — out of collected cash — for everyone else)."""

    def add_expense(self, username, name, paid=False):
        self.login(username)
        data = {'villa': self.villa.pk, 'name': name, 'amount': '100.00', 'date': THIS_MONTH.isoformat()}
        if paid:
            data.update(mark_paid='on', payment_method='cash')
        r = self.client.post(reverse('expenses:create'), data)
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['form'].errors)
        from apps.expenses.models import Expense
        return Expense.objects.get(category__name=name)

    def test_payer_is_the_person_recording_it(self):
        self.login('owner')
        self.assertNotIn('paid_by', self.client.get(reverse('expenses:create')).context['form'].fields)
        cases = [('owner', 'A', False, ('owner', None, False)), ('manager', 'B', False, ('owner', None, False)), ('staff', 'C', False, ('staff', None, False)),
                 ('owner', 'D', True, ('owner', self.owner, False)), ('manager', 'E', True, ('owner', self.manager, False)), ('staff', 'F', True, ('staff', self.staff, True))]
        for username, name, paid, (side, payer, to_verify) in cases:
            e = self.add_expense(username, name, paid)
            self.assertEqual((e.paid_by, e.paid_by_user, e.awaiting_verification), (side, payer, to_verify), name)
        # Staff paying an owner-side bill later makes it theirs (from their cash), awaiting verification.
        self.login('staff')
        from apps.expenses.models import Expense
        a = Expense.objects.get(category__name='A')
        self.client.post(reverse('expenses:quick_paid', args=[a.pk]))
        a.refresh_from_db()
        self.assertEqual((a.paid_by, a.paid_by_user, a.awaiting_verification), ('staff', self.staff, True))

    def test_fixed_expense_side_follows_who_sets_it_up(self):
        from apps.expenses.models import RecurringExpense
        self.login('staff')
        self.assertNotIn('paid_by', self.client.get(reverse('expenses:recurring_create', args=[self.villa.pk])).context['form'].fields)
        self.client.post(reverse('expenses:recurring_create', args=[self.villa.pk]), {'name': 'Gas', 'amount': '90', 'auto_debit': 'on'})
        self.login('manager')
        self.client.post(reverse('expenses:recurring_create', args=[self.villa.pk]), {'name': 'Villa rent', 'amount': '9000', 'auto_debit': 'on'})
        sides = {r.category.name: (r.paid_by, r.auto_debit) for r in RecurringExpense.objects.select_related('category')}
        self.assertEqual(sides, {'Gas': ('staff', False), 'Villa rent': ('owner', True)})

    def test_staff_bills_due_are_the_bills_staff_may_pay(self):
        self.add_expense('owner', 'Electricity')          # owner-created, but staff may pay it
        self.add_expense('staff', 'Cleaning')             # staff-created
        self.add_expense('owner', 'Villa rent')           # landlord rent: owner only
        self.login('staff')
        ctx = self.client.get(reverse('dashboard:home')).context
        self.assertEqual((ctx['staff_bills_count'], ctx['staff_bills_due']), (2, Decimal('200.00')))
        self.assertIsNotNone(ctx['period_pnl'])

    def test_dashboard_expense_alerts_open_the_matching_all_time_list(self):
        from apps.expenses.services import create_expense, get_or_create_category
        old_month = (LAST_MONTH - timedelta(days=1)).replace(day=1)
        create_expense(villa=self.villa, category=get_or_create_category(name='Old bill'), amount=Decimal('70.00'), date=old_month, due_date=old_month, created_by=self.owner)
        self.add_expense('owner', 'Villa rent')  # unpaid, but owner-only
        self.login('owner')
        alert = [a for a in self.client.get(reverse('dashboard:home')).context['needs_attention'] if a['label'] == 'Overdue expenses'][0]
        listing = self.client.get(alert['url'])
        self.assertEqual((listing.context['period'], [e.category.name for e in listing.context['page_obj']]), ('all', ['Old bill']))
        self.assertContains(listing, 'All time')
        self.login('staff')
        bills = [a for a in self.client.get(reverse('dashboard:home')).context['needs_attention'] if a['label'].startswith('Staff bills due')][0]
        listing = self.client.get(bills['url'])
        self.assertEqual((bills['count'], [e.category.name for e in listing.context['page_obj']]), (1, ['Old bill']))

    def test_handover_page_cards_per_role(self):
        [payment] = collect_rent(partition=self.p1, year=THIS_MONTH.year, month=THIS_MONTH.month, method='cash', collected_by=self.staff)
        self.login('staff')
        page = self.client.get(reverse('cash:handover_list'))
        self.assertEqual((page.context['my_outstanding_cash'], page.context['my_pending_amount']), (RENT, ZERO))
        self.assertContains(page, 'Ready to hand over')
        self.assertNotContains(page, 'Awaiting your confirmation')  # staff have nothing to confirm
        handover = submit_cash_handover(staff=self.staff, payments=[payment], submitted_by=self.staff)
        page = self.client.get(reverse('cash:handover_list'))
        self.assertEqual(page.context['my_pending_amount'], RENT)  # the staff member's own pending card updates
        # The owner never submits handovers: no "My cash" / "Ready to hand over", but what is waiting for them.
        self.login('owner')
        page = self.client.get(reverse('cash:handover_list'))
        self.assertNotContains(page, 'Ready to hand over')
        self.assertNotContains(page, 'My cash')
        self.assertEqual((page.context['to_confirm_amount'], page.context['to_confirm_count'], page.context['cash_with_staff']), (RENT, 1, RENT))
        self.assertContains(page, 'Awaiting your confirmation')
        self.assertEqual(page.context['owner_cash']['total'], ZERO)  # nothing in the owner's hands yet
        collect_rent(partition=self.p2, year=THIS_MONTH.year, month=THIS_MONTH.month, method='cash', collected_by=self.owner)
        confirm_cash_handover(handover=handover, confirmed_amount=RENT, confirmed_by=self.owner)
        page = self.client.get(reverse('cash:handover_list'))
        self.assertEqual((page.context['to_confirm_amount'], page.context['cash_with_staff']), (0, ZERO))
        self.assertEqual(page.context['owner_cash'], {'direct': RENT, 'from_staff': RENT, 'total': RENT * 2})
        self.assertContains(page, 'Cash with owner')

    def test_accountability_lists_every_cash_collector_not_only_villa_staff(self):
        collect_rent(partition=self.p1, year=THIS_MONTH.year, month=THIS_MONTH.month, method='cash', collected_by=self.manager)
        self.login('owner')
        rows = {r['staff'].username: r for r in self.client.get(reverse('cash:staff_accountability')).context['rows']}
        self.assertEqual((rows['manager']['collected'], rows['manager']['holding']), (RENT, RENT))
        self.assertIn('staff', rows)

    def test_rent_charge_is_changed_only_through_the_tenant(self):
        from apps.billing.forms import ChargeForm
        self.assertFalse(ChargeForm(partition=self.p1).fields['charge_type'].queryset.filter(name__iexact='Rent').exists())
        rent = Charge.objects.get(partition=self.p1, charge_type__name='Rent')
        self.login('owner')
        self.client.post(reverse('billing:charge_cancel', args=[rent.pk]), {'reason': 'x'})
        rent.refresh_from_db()
        self.assertTrue(rent.is_active)


class ChangedPagesRenderTests(VillaStatsBase):

    def test_every_changed_page_renders(self):
        tenant = Tenant.objects.get(partition=self.p1)
        partner = Partner.objects.create(name='P', created_by=self.owner)
        PartnerVillaShare.objects.create(partner=partner, villa=self.villa, percentage=Decimal('40.00'))
        empty = create_partition(villa=self.villa, name='EMPTY', created_by=self.owner)
        self.login('owner')
        self.collect(self.p1, THIS_MONTH, amount=Decimal('1000.00'))
        month = {'year': THIS_MONTH.year, 'month': THIS_MONTH.month}
        pages = [
            (reverse('dashboard:home'), {}),
            (reverse('villas:villa_list'), month),
            (reverse('villas:villa_detail', args=[self.villa.pk]), {'period': 'year', 'year': THIS_MONTH.year}),
            (reverse('villas:partition_detail', args=[self.p1.pk]), month),
            (reverse('billing:payment_list'), {**month, 'tab': 'collected'}),
            (reverse('billing:payment_list'), {**month, 'tab': 'collected', 'collector': 'staff'}),
            (reverse('billing:payment_list'), {**month, 'tab': 'pending'}),
            (reverse('tenancy:detail', args=[tenant.pk]), {}),
            (reverse('tenancy:edit', args=[tenant.pk]), {}),
            (reverse('tenancy:create', args=[empty.pk]), {}),
            (reverse('tenancy:deposit_edit', args=[tenant.pk]), {}),
            (reverse('businesses:partner_detail', args=[partner.pk]), month),
            (reverse('businesses:partner_list'), month),
            (reverse('businesses:villa_partner_payout_batch', args=[self.villa.pk]), month),
            (reverse('expenses:list'), month),
        ]
        for url, params in pages:
            self.assertEqual(self.client.get(url, params).status_code, 200, url)

    def test_onboarding_form_sets_rent_and_partial_advance(self):
        partition = create_partition(villa=self.villa, name='NEW', created_by=self.owner)
        self.login('owner')
        r = self.client.post(reverse('tenancy:create', args=[partition.pk]), {'name': 'Ali', 'move_in_date': TODAY.isoformat(), 'monthly_rent': '1300.00', 'deposit': '1300.00', 'deposit_paid': '1000.00', 'deposit_method': 'cash'})
        self.assertEqual(r.status_code, 302)
        tenant = Tenant.objects.get(name='Ali')
        self.assertEqual((tenant.monthly_rent, tenant.deposit_paid, tenant.deposit_pending), (Decimal('1300.00'), Decimal('1000.00'), Decimal('300.00')))
        r = self.client.post(reverse('tenancy:edit', args=[tenant.pk]), {'name': 'Ali', 'move_in_date': TODAY.isoformat(), 'monthly_rent': '1300.00', 'deposit': '1300.00'})
        self.assertEqual(r.status_code, 302)
        tenant.refresh_from_db()
        self.assertEqual(tenant.deposit_paid, Decimal('1000.00'))  # editing never retypes what was collected


class PartitionPageTests(VillaStatsBase):

    def test_partition_cards_and_lists_agree(self):
        from apps.expenses.services import create_expense, get_or_create_category
        self.login('owner')
        self.collect(self.p1, THIS_MONTH, amount=Decimal('1000.00'))
        for i in range(10):
            create_expense(villa=self.villa, partition=self.p1, category=get_or_create_category(name=f'Fix {i}'), amount=Decimal('10.00'), date=THIS_MONTH, created_by=self.owner)
        tenant = Tenant.objects.get(partition=self.p1)
        update_tenant(tenant=tenant, updated_by=self.owner, monthly_rent=Decimal('2500.00'))
        r = self.client.get(reverse('villas:partition_detail', args=[self.p1.pk]), {'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month})
        ctx = r.context
        self.assertEqual((ctx['pnl']['income'], ctx['pnl']['expenses']), (Decimal('1000.00'), Decimal('100.00')))
        self.assertEqual(sum(e.amount for e in ctx['recent_expenses'] if e.status == 'active'), ctx['pnl']['expenses'])
        self.assertEqual((ctx['rent']['current'], ctx['rent']['upcoming'], ctx['rent']['upcoming_from']), (RENT, Decimal('2500.00'), NEXT_MONTH))
        self.assertContains(r, f"from {NEXT_MONTH.strftime('%B %Y')}")
        self.assertContains(r, f"Collected QAR 1,000.00 on {TODAY.strftime('%d %b %Y')}")
        tile = [p for p in self.page(THIS_MONTH)['partitions'] if p.pk == self.p1.pk][0]
        self.assertEqual((tile.rent_schedule['current'], tile.pnl['income']), (RENT, Decimal('1000.00')))


class VillaCashBoxTests(VillaStatsBase):

    def test_manager_cash_is_held_until_handed_over_and_never_double_counted(self):
        [payment] = collect_rent(partition=self.p1, year=THIS_MONTH.year, month=THIS_MONTH.month, method='cash', collected_by=self.manager)
        cash = cash_selectors.villa_cash_position(self.villa)
        self.assertEqual((cash['collected'], cash['received_by_owner'], cash['holding']), (RENT, ZERO, RENT))
        handover = submit_cash_handover(staff=self.manager, payments=[payment], submitted_by=self.manager)
        confirm_cash_handover(handover=handover, confirmed_amount=RENT, confirmed_by=self.owner)
        cash = cash_selectors.villa_cash_position(self.villa)
        self.assertEqual((cash['received_by_owner'], cash['holding']), (RENT, ZERO))

    def test_short_handover_stays_with_staff(self):
        [payment] = collect_rent(partition=self.p1, year=THIS_MONTH.year, month=THIS_MONTH.month, method='cash', collected_by=self.staff)
        handover = submit_cash_handover(staff=self.staff, payments=[payment], submitted_by=self.staff)
        confirm_cash_handover(handover=handover, confirmed_amount=Decimal('2000.00'), confirmed_by=self.owner)
        cash = cash_selectors.villa_cash_position(self.villa)
        self.assertEqual((cash['received_by_owner'], cash['short_in_handovers'], cash['holding']), (Decimal('2000.00'), Decimal('300.00'), Decimal('300.00')))

    def test_owner_cash_goes_straight_to_owner(self):
        collect_rent(partition=self.p1, year=THIS_MONTH.year, month=THIS_MONTH.month, method='cash', collected_by=self.owner)
        cash = cash_selectors.villa_cash_position(self.villa)
        self.assertEqual((cash['received_by_owner'], cash['holding']), (RENT, ZERO))
