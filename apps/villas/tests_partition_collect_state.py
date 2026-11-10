"""The partition page offers only the collections that are really pending, in step with the Collections page and the villa page."""
from django.urls import reverse

from apps.tenancy.services import update_tenant
from .tests_villa_stats import RENT, THIS_MONTH, VillaStatsBase


class PartitionCollectState(VillaStatsBase):

    def setUp(self):
        super().setUp()
        self.login('owner')
        self.tenant = self.p2.current_tenant
        update_tenant(tenant=self.tenant, updated_by=self.owner, deposit=3000)

    def page(self):
        r = self.client.get(reverse('villas:partition_detail', args=[self.p2.pk]), {'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month})
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def collect_rent(self):
        self.client.post(reverse('billing:rent_collect', args=[self.p2.pk]), {'year': THIS_MONTH.year, 'month': THIS_MONTH.month, 'method': 'cash', 'amount': str(RENT), 'next': '/'})

    def collect_advance(self):
        self.client.post(reverse('tenancy:deposit_edit', args=[self.tenant.pk]), {'deposit': '3000.00', 'amount': '3000.00', 'method': 'cash', 'collected_at': THIS_MONTH.isoformat(), 'next': '/'})

    def test_both_pending_offers_both(self):
        html = self.page()
        self.assertIn('Rent pending', html)
        self.assertIn('Collect rent', html)
        self.assertIn('openRentCollectModal(this)', html)      # opens the popup, not the Collections page
        self.assertIn('id="rent-collect-modal"', html)
        self.assertIn('Collect advance', html)

    def test_rent_collected_stops_offering_rent_but_still_offers_the_advance(self):
        self.collect_rent()
        html = self.page()
        self.assertIn('Rent collected', html)
        self.assertNotIn('>Collect rent<', html)
        self.assertIn('Collect advance', html)

    def test_advance_collected_stops_offering_the_advance_but_still_offers_the_rent(self):
        self.collect_advance()
        html = self.page()
        self.assertIn('Advance collected', html)
        self.assertNotIn('Collect advance', html)
        self.assertIn('Collect rent', html)

    def test_everything_collected_offers_nothing(self):
        self.collect_rent()
        self.collect_advance()
        html = self.page()
        self.assertIn('Rent collected', html)
        self.assertIn('Advance collected', html)
        self.assertNotIn('>Collect rent<', html)
        self.assertNotIn('Collect advance', html)


class BeforeMoveIn(VillaStatsBase):
    """A month that ends before the tenant moved in shows nothing to collect."""

    def test_a_month_before_move_in_has_no_collect_actions_or_amounts(self):
        from datetime import date
        from apps.tenancy.services import create_tenant
        from apps.villas.services import create_partition
        self.login('owner')
        part = create_partition(villa=self.villa, name='Late', created_by=self.owner)
        move_in = date(THIS_MONTH.year, THIS_MONTH.month, 1)
        tenant = create_tenant(partition=part, name='Late Tenant', move_in_date=move_in, monthly_rent=RENT, deposit=1000, deposit_paid=500, created_by=self.owner)
        earlier = (move_in.replace(day=1) - __import__('datetime').timedelta(days=40)).replace(day=1)
        url = reverse('villas:partition_detail', args=[part.pk])
        before = self.client.get(url, {'period': 'month', 'year': earlier.year, 'month': earlier.month}).content.decode()
        self.assertIn('Moves in on', before)
        for text in ('Collect rent', 'Collect advance', 'received', 'Advance collected'):
            self.assertNotIn(text, before)
        now = self.client.get(url, {'period': 'month', 'year': move_in.year, 'month': move_in.month}).content.decode()
        self.assertNotIn('Moves in on', now)
        self.assertIn('Collect rent', now)
