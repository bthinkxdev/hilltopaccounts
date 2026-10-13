from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Assignment, User
from apps.businesses.models import Business
from apps.villas.models import Partition, Villa

class ListingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = User.objects.create_user('owner', password='x', is_owner=True)
        biz = Business.objects.create(name='B', created_by=owner)
        cls.villa = Villa.objects.create(business=biz, name='V', address='Doha', created_by=owner)
        cls.part = Partition.objects.create(villa=cls.villa, name='P1', rent=1500, created_by=owner)
        staff = User.objects.create_user('staff', password='x', whatsapp_number='5551 2345')
        Assignment.objects.create(user=staff, role='villa_staff', villa=cls.villa, created_by=owner)

    def test_public_pages_and_whatsapp(self):
        r = self.client.get(reverse('listings:list'))
        self.assertContains(r, 'P1')
        r = self.client.get(reverse('listings:detail', args=[self.part.pk]))
        self.assertContains(r, 'https://wa.me/97155512345')

    def test_archived_hidden(self):
        Partition.objects.filter(pk=self.part.pk).update(status='archived')
        self.assertEqual(self.client.get(reverse('listings:detail', args=[self.part.pk])).status_code, 404)

    def test_number_normalising(self):
        from apps.listings.views import _whatsapp_number as n
        self.assertEqual(n('050 123 4567'), '971501234567')
        self.assertEqual(n('+971 50 123 4567'), '971501234567')
        self.assertEqual(n('00971501234567'), '971501234567')
        self.assertEqual(n('501234567'), '971501234567')
