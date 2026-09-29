from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User
from apps.audit.models import Action, AuditLog
from apps.businesses.services import create_business
from apps.tenancy.services import create_tenant, move_out_tenant
from .models import Partition, Villa
from .services import archive_partition, archive_villa, create_partition, create_villa, update_partition, update_villa

class VillaAndPartitionCreateUIRegressionTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', is_owner=True)
        self.client.force_login(self.owner)
        self.business = create_business(name='Business A', created_by=self.owner)
        self.villa = create_villa(business=self.business, name='Villa A', created_by=self.owner)

    def test_add_villa_page_loads_without_a_preselected_business(self):
        r = self.client.get(reverse('villas:villa_create'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Business A')

    def test_can_create_villa_by_picking_business_in_the_form(self):
        r = self.client.post(reverse('villas:villa_create'), {'business': self.business.pk, 'name': 'Villa B'})
        villa = Villa.objects.get(name='Villa B')
        self.assertRedirects(r, reverse('villas:villa_detail', args=[villa.pk]))

    def test_add_partition_page_loads_without_a_preselected_villa(self):
        r = self.client.get(reverse('villas:partition_create'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Villa A')

    def test_can_create_partition_by_picking_villa_in_the_form(self):
        r = self.client.post(reverse('villas:partition_create'), {'villa': self.villa.pk, 'name': 'Unit 9'})
        partition = Partition.objects.get(name='Unit 9')
        self.assertRedirects(r, reverse('villas:partition_detail', args=[partition.pk]))

    def test_partition_create_from_a_specific_villa_has_no_redundant_picker(self):
        r = self.client.get(reverse('villas:partition_create', args=[self.villa.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, '<select name="villa"')

    def test_villas_list_page_offers_add_villa_button(self):
        r = self.client.get(reverse('villas:villa_list'))
        self.assertContains(r, reverse('villas:villa_create'))

    def test_partitions_list_page_offers_add_partition_button(self):
        r = self.client.get(reverse('villas:partition_list'))
        self.assertContains(r, reverse('villas:partition_create'))

class VillaServiceTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', is_owner=True)
        self.business = create_business(name='Business A', created_by=self.owner)

    def test_create_villa_writes_audit_entry_with_business_and_villa(self):
        villa = create_villa(business=self.business, name='Villa 1', created_by=self.owner)
        entry = AuditLog.objects.get(action=Action.VILLA_CREATED)
        self.assertEqual(entry.business, self.business)
        self.assertEqual(entry.villa, villa)

    def test_update_villa_captures_old_and_new_value(self):
        villa = create_villa(business=self.business, name='Villa 1', created_by=self.owner)
        update_villa(villa=villa, name='Villa 1 Renamed', updated_by=self.owner)
        entry = AuditLog.objects.get(action=Action.VILLA_UPDATED)
        self.assertEqual(entry.old_value['name'], 'Villa 1')
        self.assertEqual(entry.new_value['name'], 'Villa 1 Renamed')

    def test_archive_never_deletes_the_row(self):
        villa = create_villa(business=self.business, name='Villa 1', created_by=self.owner)
        archive_villa(villa=villa, archived_by=self.owner)
        villa.refresh_from_db()
        self.assertTrue(villa.is_archived)
        self.assertTrue(Villa.objects.filter(pk=villa.pk).exists())

    def test_duplicate_name_within_same_business_is_rejected(self):
        create_villa(business=self.business, name='Villa 1', created_by=self.owner)
        with self.assertRaises(ValidationError):
            create_villa(business=self.business, name='Villa 1', created_by=self.owner)

    def test_same_name_allowed_in_a_different_business(self):
        other_business = create_business(name='Business B', created_by=self.owner)
        create_villa(business=self.business, name='Villa 1', created_by=self.owner)
        villa = create_villa(business=other_business, name='Villa 1', created_by=self.owner)
        self.assertIsNotNone(villa.pk)

class PartitionServiceTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', is_owner=True)
        self.business = create_business(name='Business A', created_by=self.owner)
        self.villa = create_villa(business=self.business, name='Villa 1', created_by=self.owner)

    def test_create_partition_writes_audit_entry(self):
        partition = create_partition(villa=self.villa, name='Unit 1', created_by=self.owner)
        entry = AuditLog.objects.get(action=Action.PARTITION_CREATED)
        self.assertEqual(entry.villa, self.villa)
        self.assertEqual(entry.business, self.business)
        self.assertEqual(entry.content_object, partition)

    def test_new_partition_is_vacant(self):
        partition = create_partition(villa=self.villa, name='Unit 1', created_by=self.owner)
        self.assertFalse(partition.is_occupied)
        self.assertIsNone(partition.current_tenant)

    def test_partition_with_active_tenant_is_occupied(self):
        partition = create_partition(villa=self.villa, name='Unit 1', created_by=self.owner)
        tenant = create_tenant(partition=partition, name='Ahmed Ali', move_in_date=date(2026, 1, 1), monthly_rent=Decimal('3500.00'), created_by=self.owner)
        partition.refresh_from_db()
        self.assertTrue(partition.is_occupied)
        self.assertEqual(partition.current_tenant, tenant)

    def test_partition_becomes_vacant_after_move_out(self):
        partition = create_partition(villa=self.villa, name='Unit 1', created_by=self.owner)
        tenant = create_tenant(partition=partition, name='Ahmed Ali', move_in_date=date(2026, 1, 1), monthly_rent=Decimal('3500.00'), created_by=self.owner)
        move_out_tenant(tenant=tenant, move_out_date=date(2026, 6, 1), moved_out_by=self.owner)
        self.assertFalse(partition.is_occupied)

    def test_update_partition_captures_old_and_new_value(self):
        partition = create_partition(villa=self.villa, name='Unit 1', created_by=self.owner)
        update_partition(partition=partition, name='Unit 1 Renamed', updated_by=self.owner)
        entry = AuditLog.objects.get(action=Action.PARTITION_UPDATED)
        self.assertEqual(entry.old_value['name'], 'Unit 1')
        self.assertEqual(entry.new_value['name'], 'Unit 1 Renamed')

    def test_archive_never_deletes_the_row(self):
        partition = create_partition(villa=self.villa, name='Unit 1', created_by=self.owner)
        archive_partition(partition=partition, archived_by=self.owner, reason='unit demolished')
        partition.refresh_from_db()
        self.assertEqual(partition.status, Partition.Status.ARCHIVED)
        self.assertTrue(Partition.objects.filter(pk=partition.pk).exists())

    def test_duplicate_name_within_same_villa_is_rejected(self):
        create_partition(villa=self.villa, name='Unit 1', created_by=self.owner)
        with self.assertRaises(ValidationError):
            create_partition(villa=self.villa, name='Unit 1', created_by=self.owner)
