from io import StringIO
from django.core.management import call_command
from django.test import TestCase
from apps.billing.models import Invoice, Payment
from apps.businesses.models import Business
from apps.tenancy.models import Tenant
from apps.villas.models import Partition, Villa

class SeedDemoDataCommandTests(TestCase):

    def test_seed_command_populates_every_module(self):
        call_command('seed_demo_data', stdout=StringIO())
        self.assertGreater(Business.objects.count(), 0)
        self.assertGreater(Villa.objects.count(), 0)
        self.assertGreater(Partition.objects.count(), 0)
        self.assertGreater(Tenant.objects.filter(status='active').count(), 0)
        self.assertGreater(Invoice.objects.count(), 0)
        self.assertGreater(Payment.objects.count(), 0)
        occupied_ids = Partition.objects.filter(tenancies__status='active').values_list('pk', flat=True)
        self.assertTrue(Partition.objects.exclude(pk__in=occupied_ids).exists())
        statuses = {inv.status for inv in Invoice.objects.all()}
        self.assertIn('overdue', statuses)

    def test_seed_command_is_idempotent(self):
        call_command('seed_demo_data', stdout=StringIO())
        first_counts = (Business.objects.count(), Villa.objects.count(), Partition.objects.count(), Invoice.objects.count(), Payment.objects.count())
        call_command('seed_demo_data', stdout=StringIO())
        second_counts = (Business.objects.count(), Villa.objects.count(), Partition.objects.count(), Invoice.objects.count(), Payment.objects.count())
        self.assertEqual(first_counts, second_counts)
