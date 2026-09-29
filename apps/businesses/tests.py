from django.core.exceptions import ValidationError
from django.test import TestCase
from apps.accounts.models import User
from apps.audit.models import Action, AuditLog
from .models import Business
from .services import archive_business, create_business, update_business

class BusinessServiceTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', is_owner=True)

    def test_create_business_writes_audit_entry(self):
        business = create_business(name='Villa Rentals', description='Main line', created_by=self.owner)
        entry = AuditLog.objects.get(action=Action.BUSINESS_CREATED)
        self.assertEqual(entry.user, self.owner)
        self.assertEqual(entry.business, business)
        self.assertEqual(entry.new_value['name'], 'Villa Rentals')

    def test_update_business_captures_old_and_new_value(self):
        business = create_business(name='Old Name', created_by=self.owner)
        update_business(business=business, name='New Name', updated_by=self.owner)
        entry = AuditLog.objects.get(action=Action.BUSINESS_UPDATED)
        self.assertEqual(entry.old_value['name'], 'Old Name')
        self.assertEqual(entry.new_value['name'], 'New Name')

    def test_update_with_no_actual_change_does_not_log(self):
        business = create_business(name='Same Name', created_by=self.owner)
        AuditLog.objects.all().delete()
        update_business(business=business, name='Same Name', updated_by=self.owner)
        self.assertFalse(AuditLog.objects.filter(action=Action.BUSINESS_UPDATED).exists())

    def test_archive_never_deletes_the_row(self):
        business = create_business(name='To Archive', created_by=self.owner)
        archive_business(business=business, archived_by=self.owner, reason='closed for the season')
        business.refresh_from_db()
        self.assertTrue(business.is_archived)
        self.assertTrue(Business.objects.filter(pk=business.pk).exists())
        entry = AuditLog.objects.get(action=Action.BUSINESS_ARCHIVED)
        self.assertEqual(entry.reason, 'closed for the season')

    def test_duplicate_name_is_rejected(self):
        create_business(name='Unique Co', created_by=self.owner)
        with self.assertRaises(ValidationError):
            create_business(name='Unique Co', created_by=self.owner)
