from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from apps.accounts.models import User
from apps.accounts.selectors import tenants_visible_to
from apps.accounts.services import assign_villa_staff
from apps.audit.models import Action, AuditLog
from apps.businesses.services import create_business
from apps.shared.exceptions import DomainError
from apps.villas.services import create_partition, create_villa
from .exceptions import PartitionAlreadyOccupied
from .models import Tenant
from .services import create_tenant, move_out_tenant, update_tenant

class TenantServiceTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', is_owner=True)
        self.business = create_business(name='Business A', created_by=self.owner)
        self.villa = create_villa(business=self.business, name='Villa 1', created_by=self.owner)
        self.partition = create_partition(villa=self.villa, name='Unit 1', created_by=self.owner)

    def _create(self, **overrides):
        defaults = dict(partition=self.partition, name='Ahmed Ali', move_in_date=date(2026, 1, 1), monthly_rent=Decimal('3500.00'), created_by=self.owner)
        defaults.update(overrides)
        return create_tenant(**defaults)

    def test_create_tenant_writes_audit_entry(self):
        tenant = self._create()
        entry = AuditLog.objects.get(action=Action.TENANT_CREATED)
        self.assertEqual(entry.villa, self.villa)
        self.assertEqual(entry.business, self.business)
        self.assertEqual(entry.content_object, tenant)
        self.assertEqual(entry.new_value['monthly_rent'], '3500.00')

    def test_cannot_create_a_second_active_tenant_on_same_partition(self):
        self._create()
        with self.assertRaises(PartitionAlreadyOccupied):
            self._create(name='Second Tenant')

    def test_db_constraint_backs_the_service_check(self):
        self._create()
        with self.assertRaises(Exception):
            Tenant.objects.create(partition=self.partition, name='Bypassing Service', move_in_date=date(2026, 1, 1), monthly_rent=Decimal('1000.00'), created_by=self.owner)

    def test_negative_rent_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._create(monthly_rent=Decimal('-100.00'))

    def test_move_out_frees_the_partition_for_a_new_tenant(self):
        tenant = self._create()
        move_out_tenant(tenant=tenant, move_out_date=date(2026, 6, 1), moved_out_by=self.owner)
        new_tenant = self._create(name='New Tenant')
        self.assertNotEqual(tenant.pk, new_tenant.pk)
        entry = AuditLog.objects.get(action=Action.TENANT_MOVED_OUT)
        self.assertEqual(entry.old_value['status'], 'active')
        self.assertEqual(entry.new_value['status'], 'moved_out')

    def test_cannot_move_out_an_already_moved_out_tenant(self):
        tenant = self._create()
        move_out_tenant(tenant=tenant, move_out_date=date(2026, 6, 1), moved_out_by=self.owner)
        with self.assertRaises(DomainError):
            move_out_tenant(tenant=tenant, move_out_date=date(2026, 7, 1), moved_out_by=self.owner)

    def test_move_out_before_move_in_is_rejected(self):
        tenant = self._create()
        with self.assertRaises(DomainError):
            move_out_tenant(tenant=tenant, move_out_date=date(2025, 12, 1), moved_out_by=self.owner)

    def test_update_tenant_captures_old_and_new_value(self):
        tenant = self._create()
        update_tenant(tenant=tenant, updated_by=self.owner, mobile='+974 5555 1234')
        entry = AuditLog.objects.get(action=Action.TENANT_UPDATED)
        self.assertEqual(entry.old_value['mobile'], '')
        self.assertEqual(entry.new_value['mobile'], '+974 5555 1234')

    def test_no_hard_delete(self):
        tenant = self._create()
        move_out_tenant(tenant=tenant, move_out_date=date(2026, 6, 1), moved_out_by=self.owner)
        self.assertTrue(Tenant.objects.filter(pk=tenant.pk).exists())

class TenantRBACScopingTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', is_owner=True)
        self.staff = User.objects.create_user('staff')
        self.business = create_business(name='Business A', created_by=self.owner)
        self.villa_1 = create_villa(business=self.business, name='Villa 1', created_by=self.owner)
        self.villa_2 = create_villa(business=self.business, name='Villa 2', created_by=self.owner)
        self.partition_1 = create_partition(villa=self.villa_1, name='Unit 1', created_by=self.owner)
        self.partition_2 = create_partition(villa=self.villa_2, name='Unit 1', created_by=self.owner)
        self.tenant_1 = create_tenant(partition=self.partition_1, name='In Villa 1', move_in_date=date(2026, 1, 1), monthly_rent=Decimal('3000.00'), created_by=self.owner)
        self.tenant_2 = create_tenant(partition=self.partition_2, name='In Villa 2', move_in_date=date(2026, 1, 1), monthly_rent=Decimal('3000.00'), created_by=self.owner)
        assign_villa_staff(user=self.staff, villa=self.villa_1, assigned_by=self.owner)

    def test_staff_sees_only_tenants_in_assigned_villa(self):
        visible = tenants_visible_to(self.staff)
        self.assertQuerySetEqual(visible, [self.tenant_1], transform=lambda t: t)

    def test_owner_sees_all_tenants(self):
        self.assertEqual(tenants_visible_to(self.owner).count(), 2)
