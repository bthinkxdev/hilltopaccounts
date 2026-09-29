from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from apps.audit.models import Action, AuditLog
from apps.businesses.services import create_business
from apps.villas.services import create_villa
from .models import Assignment, User
from .services import assign_business_manager, assign_villa_staff, create_user, remove_assignment
from . import selectors

class CreateUserAuditTests(TestCase):

    def test_create_user_logs_exactly_once(self):
        owner = User.objects.create_user('owner', is_owner=True)
        new_user = create_user(username='newstaff', created_by=owner, password='Sup3r-Secret-Pass!')
        self.assertEqual(AuditLog.objects.filter(action=Action.USER_CREATED, object_id=str(new_user.pk)).count(), 1)

    def test_create_user_attributes_to_the_explicit_actor(self):
        owner = User.objects.create_user('owner', is_owner=True)
        create_user(username='newstaff2', created_by=owner, password='Sup3r-Secret-Pass!')
        entry = AuditLog.objects.get(action=Action.USER_CREATED, new_value__username='newstaff2')
        self.assertEqual(entry.user, owner)

    def test_create_user_with_password_can_log_in(self):
        owner = User.objects.create_user('owner', is_owner=True)
        create_user(username='newstaff3', created_by=owner, password='Sup3r-Secret-Pass!')
        self.assertTrue(self.client.login(username='newstaff3', password='Sup3r-Secret-Pass!'))

class AssignmentScopeConstraintTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', is_owner=True)
        self.staff = User.objects.create_user('staff')
        self.business = create_business(name='Biz', created_by=self.owner)

    def test_business_manager_without_business_is_rejected(self):
        with self.assertRaises(ValidationError):
            Assignment(user=self.staff, role=Assignment.Role.BUSINESS_MANAGER, business=None, created_by=self.owner).full_clean()

    def test_villa_staff_without_villa_is_rejected(self):
        with self.assertRaises(ValidationError):
            Assignment(user=self.staff, role=Assignment.Role.VILLA_STAFF, villa=None, created_by=self.owner).full_clean()

class RBACScopingTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', is_owner=True)
        self.manager = User.objects.create_user('manager')
        self.staff = User.objects.create_user('staff')
        self.outsider = User.objects.create_user('outsider')
        self.business_a = create_business(name='Business A', created_by=self.owner)
        self.business_b = create_business(name='Business B', created_by=self.owner)
        self.villa_1 = create_villa(business=self.business_a, name='Villa 1', created_by=self.owner)
        self.villa_2 = create_villa(business=self.business_a, name='Villa 2', created_by=self.owner)
        assign_business_manager(user=self.manager, business=self.business_a, assigned_by=self.owner)
        assign_villa_staff(user=self.staff, villa=self.villa_1, assigned_by=self.owner)

    def test_owner_sees_everything(self):
        self.assertEqual(selectors.businesses_visible_to(self.owner).count(), 2)
        self.assertEqual(selectors.villas_visible_to(self.owner).count(), 2)

    def test_manager_sees_only_assigned_business(self):
        businesses = selectors.businesses_visible_to(self.manager)
        self.assertQuerySetEqual(businesses, [self.business_a], transform=lambda b: b)
        villas = selectors.villas_visible_to(self.manager)
        self.assertEqual(set(villas), {self.villa_1, self.villa_2})

    def test_staff_sees_only_assigned_villa_not_sibling(self):
        villas = selectors.villas_visible_to(self.staff)
        self.assertQuerySetEqual(villas, [self.villa_1], transform=lambda v: v)

    def test_outsider_sees_nothing(self):
        self.assertEqual(selectors.businesses_visible_to(self.outsider).count(), 0)
        self.assertEqual(selectors.villas_visible_to(self.outsider).count(), 0)

    def test_manager_a_cannot_see_business_b(self):
        self.assertNotIn(self.business_b, selectors.businesses_visible_to(self.manager))

    def test_remove_assignment_revokes_visibility(self):
        assignment = Assignment.objects.get(user=self.staff, role=Assignment.Role.VILLA_STAFF)
        remove_assignment(assignment=assignment, removed_by=self.owner)
        self.assertEqual(selectors.villas_visible_to(self.staff).count(), 0)

class DashboardAccessTests(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', is_owner=True)
        self.staff = User.objects.create_user('staff', password='Passw0rd!2026')
        self.business = create_business(name='Business A', created_by=self.owner)
        self.villa_1 = create_villa(business=self.business, name='Villa 1', created_by=self.owner)
        self.villa_2 = create_villa(business=self.business, name='Villa 2', created_by=self.owner)
        assign_villa_staff(user=self.staff, villa=self.villa_1, assigned_by=self.owner)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse('dashboard:home'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:login'), response.url)

    def test_staff_dashboard_shows_only_assigned_villa(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('dashboard:home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Villa 1')
        self.assertNotContains(response, 'Villa 2')

    def test_staff_dashboard_never_receives_financial_kpis_in_context(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('dashboard:home'))
        self.assertFalse(response.context['can_view_financials'])
        self.assertNotIn('expected_revenue', response.context)
        self.assertNotContains(response, 'Financial summary')
        self.assertNotContains(response, 'Net Profit')

    def test_owner_dashboard_shows_financial_kpis(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('dashboard:home'))
        self.assertTrue(response.context['can_view_financials'])
        self.assertContains(response, 'Financial summary')

class AuthAuditTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('someone', password='Passw0rd!2026')

    def test_successful_login_is_audited(self):
        client = Client()
        client.post(reverse('accounts:login'), {'username': 'someone', 'password': 'Passw0rd!2026'})
        self.assertTrue(AuditLog.objects.filter(action=Action.AUTH_LOGIN, user=self.user).exists())

    def test_failed_login_is_audited_against_the_matching_account(self):
        client = Client()
        client.post(reverse('accounts:login'), {'username': 'someone', 'password': 'wrong'})
        entry = AuditLog.objects.get(action=Action.AUTH_FAILED_LOGIN)
        self.assertEqual(entry.user, self.user)

    def test_logout_is_audited(self):
        client = Client()
        client.force_login(self.user)
        client.post(reverse('accounts:logout'))
        self.assertTrue(AuditLog.objects.filter(action=Action.AUTH_LOGOUT, user=self.user).exists())
