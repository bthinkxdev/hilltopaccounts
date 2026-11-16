"""Profit and loss is for the Admin and for a partner of that villa (and the Business Manager); villa staff do not see it."""
from decimal import Decimal

from django.urls import reverse

from apps.accounts.services import link_partner
from apps.businesses.models import Partner, PartnerVillaShare
from .tests_villa_stats import THIS_MONTH, VillaStatsBase


class PnlScopeTests(VillaStatsBase):

    def detail(self):
        return self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), {'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month})

    def test_villa_staff_do_not_see_the_profit_and_loss(self):
        self.login('staff')
        r = self.detail()
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context['can_view_financials'])
        self.assertNotIn('pnl', r.context)
        self.assertNotContains(r, 'Expense Details')

    def test_admin_and_business_manager_see_it(self):
        for who in ('owner', 'manager'):
            self.login(who)
            r = self.detail()
            self.assertTrue(r.context['can_view_financials'], who)
            self.assertIn('pnl', r.context, who)
            self.assertContains(r, 'Expense Details')

    def test_a_partner_of_the_villa_sees_it_and_only_for_that_villa(self):
        partner = Partner.objects.create(name='P', created_by=self.owner)
        PartnerVillaShare.objects.create(partner=partner, villa=self.villa, percentage=Decimal('25.00'))
        self.login('staff')
        self.assertFalse(self.detail().context['can_view_financials'])  # not linked yet
        link_partner(user=self.staff, partner=partner, changed_by=self.owner)
        r = self.detail()
        self.assertTrue(r.context['can_view_financials'])
        self.assertContains(r, 'Expense Details')
        self.assertEqual(r.context['my_partner_share']['percentage'], Decimal('25.00'))

    def test_a_linked_partner_without_a_share_in_this_villa_does_not(self):
        partner = Partner.objects.create(name='P', created_by=self.owner)
        link_partner(user=self.staff, partner=partner, changed_by=self.owner)  # linked, but holds no share here
        self.login('staff')
        self.assertFalse(self.detail().context['can_view_financials'])

    def test_the_villa_list_and_dashboard_follow_the_same_rule(self):
        self.login('staff')
        listing = self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month})
        self.assertIsNone(listing.context['totals'])
        self.assertTrue(all(v.pnl is None for v in listing.context['page_obj']))
        home = self.client.get(reverse('dashboard:home'), {'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month})
        self.assertFalse(home.context.get('period_pnl'))
        self.login('owner')
        listing = self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month})
        self.assertIsNotNone(listing.context['totals'])
        self.assertTrue(all(v.pnl is not None for v in listing.context['page_obj']))

    def test_a_partner_sees_the_figures_for_their_villa_on_the_list_and_dashboard(self):
        partner = Partner.objects.create(name='P', created_by=self.owner)
        PartnerVillaShare.objects.create(partner=partner, villa=self.villa, percentage=Decimal('25.00'))
        link_partner(user=self.staff, partner=partner, changed_by=self.owner)
        self.login('staff')
        listing = self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month})
        self.assertIsNotNone(listing.context['totals'])
        self.assertTrue(all(v.pnl is not None for v in listing.context['page_obj']))
        home = self.client.get(reverse('dashboard:home'), {'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month})
        self.assertTrue(home.context.get('period_pnl'))


class InactivePartnerTests(VillaStatsBase):
    """A partner made inactive (with a share on a villa) must change nothing in anyone's figures."""

    def test_an_inactive_partner_share_is_ignored_everywhere(self):
        partner = Partner.objects.create(name='Mistake', is_active=False, created_by=self.owner)
        PartnerVillaShare.objects.create(partner=partner, villa=self.villa, percentage=Decimal('30.00'))
        self.login('owner')
        period = {'period': 'month', 'year': THIS_MONTH.year, 'month': THIS_MONTH.month}
        ctx = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), period).context
        self.assertEqual(ctx['villa_partner_shares'], [])
        self.assertEqual((ctx['villa_owner_share']['percentage'], ctx['total_partner_cut'], ctx['partner_count']), (Decimal('100.00'), Decimal('0.00'), 0))
        home = self.client.get(reverse('dashboard:home'), period).context
        self.assertEqual(home['total_partner_cut'], Decimal('0.00'))
        self.assertEqual(self.client.get(reverse('businesses:partner_list'), period).context['page_obj'].paginator.count if 'page_obj' in self.client.get(reverse('businesses:partner_list'), period).context else 0, 0)
