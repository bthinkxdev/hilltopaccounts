from django.contrib.admin.sites import site
from django.test import TestCase
from apps.accounts.models import User
from . import context
from .models import Action, AuditLog
from .services import log

class AuditServiceTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('someone')

    def test_log_captures_object_reference(self):
        entry = log(user=self.user, action=Action.BUSINESS_CREATED, obj=self.user)
        self.assertEqual(entry.object_id, str(self.user.pk))
        self.assertEqual(entry.content_object, self.user)

    def test_log_without_object_leaves_content_type_blank(self):
        entry = log(user=self.user, action=Action.REPORT_VIEWED)
        self.assertIsNone(entry.content_type)
        self.assertEqual(entry.object_id, '')

    def test_context_defaults_are_empty_outside_a_request(self):
        context.clear_context()
        ctx = context.get_context()
        self.assertIsNone(ctx.ip_address)
        self.assertIsNone(ctx.actor)

class AuditLogIsAppendOnlyInAdminTests(TestCase):

    def test_admin_forbids_add_change_delete(self):
        admin = site._registry[AuditLog]
        self.assertFalse(admin.has_add_permission(request=None))
        self.assertFalse(admin.has_change_permission(request=None))
        self.assertFalse(admin.has_delete_permission(request=None))
