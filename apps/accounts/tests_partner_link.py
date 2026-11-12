"""The Partner selection bar on a staff page links a login to a partner record; a linked partner sees the partner details of the
villas where that partner has a share (and that they work in)."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.audit.models import Action, AuditLog
from apps.businesses.models import Partner, PartnerVillaShare
from apps.businesses.services import create_business
from apps.villas.services import create_villa
from . import selectors
from .models import User
from .services import assign_villa_staff, link_partner

PASSWORD = 'Passw0rd!2026'


class PartnerLinkTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', password=PASSWORD, is_owner=True)
        self.staff = User.objects.create_user('shihab', password=PASSWORD)
        self.business = create_business(name='Biz', created_by=self.owner)
        self.v1 = create_villa(business=self.business, name='Villa 1', created_by=self.owner)
        self.v2 = create_villa(business=self.business, name='Villa 2', created_by=self.owner)
        assign_villa_staff(user=self.staff, villa=self.v1, assigned_by=self.owner)
        assign_villa_staff(user=self.staff, villa=self.v2, assigned_by=self.owner)
        self.partner = Partner.objects.create(name='Shihab Partner', created_by=self.owner)
        self.url = reverse('accounts:staff_partner_link', args=[self.staff.pk])
        self.detail = reverse('accounts:staff_detail', args=[self.staff.pk])

    def login(self, name):
        self.client.logout()
        self.assertTrue(self.client.login(username=name, password=PASSWORD))

    def test_the_admin_links_and_unlinks(self):
        self.login('owner')
        self.assertRedirects(self.client.post(self.url, {'partner': self.partner.pk}), self.detail)
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.user_id, self.staff.pk)
        self.assertTrue(AuditLog.objects.filter(action=Action.PARTNER_UPDATED, reason='login linked to partner').exists())
        self.client.post(self.url, {'partner': ''})
        self.partner.refresh_from_db()
        self.assertIsNone(self.partner.user_id)
        self.assertTrue(AuditLog.objects.filter(action=Action.PARTNER_UPDATED, reason='login unlinked from partner').exists())

    def test_choosing_another_partner_replaces_the_link(self):
        other = Partner.objects.create(name='Second', created_by=self.owner)
        link_partner(user=self.staff, partner=self.partner, changed_by=self.owner)
        link_partner(user=self.staff, partner=other, changed_by=self.owner)
        self.partner.refresh_from_db(); other.refresh_from_db()
        self.assertEqual((self.partner.user_id, other.user_id), (None, self.staff.pk))

    def test_a_partner_with_another_login_cannot_be_chosen(self):
        taken = Partner.objects.create(name='Taken', user=User.objects.create_user('other'), created_by=self.owner)
        self.login('owner')
        self.client.post(self.url, {'partner': taken.pk})
        taken.refresh_from_db()
        self.assertNotEqual(taken.user_id, self.staff.pk)
        self.assertNotIn('Taken', [p.name for p in self.client.get(self.detail).context['partner_form'].fields['partner'].queryset])

    def test_only_the_admin_can_link(self):
        self.login('shihab')
        self.assertEqual(self.client.post(self.url, {'partner': self.partner.pk}).status_code, 403)

    def test_the_page_lists_the_partners_villas_and_who_can_see_them(self):
        outsider = create_villa(business=self.business, name='Villa 3', created_by=self.owner)
        PartnerVillaShare.objects.create(partner=self.partner, villa=self.v1, percentage=Decimal('20.00'))
        PartnerVillaShare.objects.create(partner=self.partner, villa=outsider, percentage=Decimal('10.00'))
        link_partner(user=self.staff, partner=self.partner, changed_by=self.owner)
        self.login('owner')
        shares = {s['villa'].name: s['sees_it'] for s in self.client.get(self.detail).context['partner_shares']}
        self.assertEqual(shares, {'Villa 1': True, 'Villa 3': False})  # Villa 3: not one of their villas yet

    def test_a_linked_partner_sees_partner_details_only_on_villas_with_a_share(self):
        PartnerVillaShare.objects.create(partner=self.partner, villa=self.v1, percentage=Decimal('20.00'))
        self.login('shihab')
        page = lambda villa: self.client.get(reverse('villas:villa_detail', args=[villa.pk])).context
        self.assertFalse(page(self.v1)['is_partner'])  # staff only: no partner section
        link_partner(user=self.staff, partner=self.partner, changed_by=self.owner)
        self.assertTrue(selectors.can_view_villa_financials(self.staff, self.v1))
        self.assertFalse(selectors.can_view_villa_financials(self.staff, self.v2))  # no share there
        with_share = page(self.v1)
        self.assertTrue(with_share['is_partner'])
        self.assertEqual(with_share['my_partner_share']['percentage'], Decimal('20.00'))
        self.assertFalse(page(self.v2)['is_partner'])  # a villa of theirs where the partner has no share: staff view only
        self.assertEqual(set(selectors.financial_villas(self.staff)), {self.v1})

    def test_unlinking_takes_the_partner_details_away_again(self):
        PartnerVillaShare.objects.create(partner=self.partner, villa=self.v1, percentage=Decimal('20.00'))
        link_partner(user=self.staff, partner=self.partner, changed_by=self.owner)
        link_partner(user=self.staff, partner=None, changed_by=self.owner)
        self.assertFalse(selectors.can_view_villa_financials(self.staff, self.v1))
        self.assertEqual(set(selectors.financial_villas(self.staff)), set())
