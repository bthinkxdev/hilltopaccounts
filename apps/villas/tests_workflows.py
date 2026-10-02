"""End-to-end HTTP workflows for the villa-as-accounting-hub flow, for the Owner and for assigned Staff."""
from datetime import date, timedelta
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User
from apps.accounts.services import assign_villa_staff
from apps.audit.models import Action, AuditLog
from apps.billing.models import ChargeType, Invoice, Payment
from apps.businesses.services import create_business
from apps.cash.models import CashHandover
from apps.expenses.models import Expense, ExpenseCategory
from apps.tenancy.models import Tenant
from apps.villas.models import Partition, Villa
from apps.villas.services import create_villa

PASSWORD = 'Passw0rd!2026'

class OwnerVillaAccountingWorkflow(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', password=PASSWORD, is_owner=True)

    def test_owner_login_to_villa_profit_and_loss(self):
        r = self.client.post(reverse('accounts:login'), {'username': 'owner', 'password': PASSWORD})
        self.assertEqual(r.status_code, 302)
        r = self.client.post(reverse('businesses:create'), {'name': 'Villa Rentals', 'description': ''})
        business = __import__('apps.businesses.models', fromlist=['Business']).Business.objects.get(name='Villa Rentals')
        self.client.post(reverse('villas:villa_create'), {'business': business.pk, 'name': 'Villa A', 'address': 'Al Waab'})
        villa = Villa.objects.get(name='Villa A')
        self.client.post(reverse('villas:partition_create', args=[villa.pk]), {'name': 'Unit 1'})
        partition = Partition.objects.get(villa=villa, name='Unit 1')
        self.client.post(reverse('tenancy:create', args=[partition.pk]), {'name': 'Ahmed Ali', 'move_in_date': '2026-01-01', 'monthly_rent': '3000.00', 'deposit': '0'})
        rent = ChargeType.objects.create(name='Rent')
        self.client.post(reverse('billing:charge_create', args=[partition.pk]), {'charge_type': rent.pk, 'amount': '3000.00', 'frequency': 'monthly', 'start_date': '2026-01-01'})
        self.client.post(reverse('billing:invoice_generate', args=[partition.pk]), {'billing_period_start': '2026-02-01', 'billing_period_end': '2026-02-28', 'issue_date': '2026-02-01', 'due_date': '2026-02-10'})
        invoice = Invoice.objects.get(partition=partition)
        self.client.post(reverse('billing:payment_record', args=[invoice.pk]), {'amount': '2000.00', 'method': 'cash', 'collected_at': '2026-02-05'})

        r = self.client.post(reverse('expenses:create_for_villa', args=[villa.pk]), {'villa': villa.pk, 'name': 'Electricity', 'amount': '450.00', 'date': '2026-02-06', 'due_date': '2026-02-20'})
        self.assertRedirects(r, reverse('villas:villa_detail', args=[villa.pk]))
        expense = Expense.objects.get(villa=villa)
        self.assertEqual((expense.business_id, expense.paid_on), (business.pk, None))
        self.assertTrue(AuditLog.objects.filter(action=Action.EXPENSE_CREATED, object_id=str(expense.pk)).exists())

        # A GET must never change state; the POST marks it paid and is audited.
        self.client.get(reverse('expenses:mark_paid', args=[expense.pk]))
        expense.refresh_from_db()
        self.assertIsNone(expense.paid_on)
        r = self.client.post(reverse('expenses:mark_paid', args=[expense.pk]), {'paid_on': '2026-02-18', 'payment_method': 'bank_transfer'})
        self.assertRedirects(r, reverse('expenses:list'))
        expense.refresh_from_db()
        self.assertEqual(expense.paid_on, date(2026, 2, 18))
        self.assertEqual(AuditLog.objects.filter(action=Action.EXPENSE_UPDATED, object_id=str(expense.pk)).count(), 1)
        r = self.client.post(reverse('expenses:mark_paid', args=[expense.pk]), {'paid_on': '2026-02-19', 'payment_method': 'cash'})
        self.assertEqual(r.status_code, 200)  # already paid: rejected with a form error, not silently overwritten
        self.assertContains(r, 'already marked paid')

        r = self.client.get(reverse('villas:villa_detail', args=[villa.pk]), {'period': 'month', 'year': 2026, 'month': 2})
        pnl = r.context['pnl']
        self.assertEqual((pnl['income'], pnl['expenses'], pnl['net'], pnl['outstanding']), (Decimal('2000.00'), Decimal('450.00'), Decimal('1550.00'), Decimal('1000.00')))
        self.assertContains(r, '1,550.00')
        r = self.client.get(reverse('villas:villa_detail', args=[villa.pk]), {'period': 'year', 'year': 2026})
        self.assertEqual(r.context['pnl']['net'], Decimal('1550.00'))
        r = self.client.get(reverse('villas:villa_detail', args=[villa.pk]), {'period': 'month', 'year': 2026, 'month': 3})
        self.assertEqual((r.context['pnl']['income'], r.context['pnl']['net']), (Decimal('0.00'), Decimal('0.00')))
        r = self.client.get(reverse('villas:villa_detail', args=[villa.pk]), {'period': 'month', 'year': 'abc', 'month': '99'})
        self.assertEqual(r.status_code, 200)

    def test_expense_name_autocomplete_prefills_latest_amount_but_stays_editable(self):
        business = create_business(name='B', created_by=self.owner)
        villa = create_villa(business=business, name='V', created_by=self.owner)
        self.client.login(username='owner', password=PASSWORD)
        self.client.post(reverse('expenses:create'), {'villa': villa.pk, 'name': 'Electricity', 'amount': '450.00', 'date': '2026-01-05'})
        r = self.client.post(reverse('expenses:create'), {'villa': villa.pk, 'name': 'electricity', 'amount': '510.00', 'date': '2026-02-05'})
        self.assertRedirects(r, reverse('expenses:list'))
        self.assertEqual(ExpenseCategory.objects.filter(name__iexact='electricity').count(), 1)
        self.assertEqual(sorted(Expense.objects.values_list('amount', flat=True)), [Decimal('450.00'), Decimal('510.00')])
        r = self.client.get(reverse('expenses:suggest'), {'name': 'ELECTRICITY', 'villa': villa.pk})
        self.assertEqual(r.json(), {'amount': '510.00'})
        self.assertEqual(self.client.get(reverse('expenses:suggest'), {'name': 'Never used'}).json(), {'amount': None})
        self.assertContains(self.client.get(reverse('expenses:create')), 'value="Electricity"')

    def test_validation_errors_are_field_level_and_nothing_is_saved(self):
        business = create_business(name='B', created_by=self.owner)
        villa = create_villa(business=business, name='V', created_by=self.owner)
        self.client.login(username='owner', password=PASSWORD)
        r = self.client.post(reverse('expenses:create'), {'villa': villa.pk, 'name': 'Wi-Fi', 'amount': '-5', 'date': '2026-02-05', 'due_date': '2026-02-01', 'mark_paid': 'on'})
        self.assertEqual(r.status_code, 200)
        form = r.context['form']
        self.assertEqual(set(form.errors), {'amount', 'due_date', 'payment_method'})
        self.assertFalse(Expense.objects.exists())

    def test_due_buckets_on_expense_list(self):
        business = create_business(name='B', created_by=self.owner)
        villa = create_villa(business=business, name='V', created_by=self.owner)
        self.client.login(username='owner', password=PASSWORD)
        today = date.today()
        for name, offset in (('Rent', -3), ('Water', 0), ('Wifi', 5)):
            self.client.post(reverse('expenses:create'), {'villa': villa.pk, 'name': name, 'amount': '100.00', 'date': (today - timedelta(days=10)).isoformat(), 'due_date': (today + timedelta(days=offset)).isoformat()})
        r = self.client.get(reverse('expenses:list'))
        summary = r.context['summary']
        self.assertEqual([summary[k]['count'] for k in ('overdue', 'today', 'upcoming', 'unpaid', 'paid')], [1, 1, 1, 3, 0])
        self.assertEqual([e.category.name for e in self.client.get(reverse('expenses:list'), {'due': 'overdue'}).context['page_obj']], ['Rent'])

class StaffVillaScopeWorkflow(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', password=PASSWORD, is_owner=True)
        self.business = create_business(name='Business A', created_by=self.owner)
        self.other_business = create_business(name='Business B', created_by=self.owner)
        self.villa = create_villa(business=self.business, name='Villa A', created_by=self.owner)
        self.other_villa = create_villa(business=self.other_business, name='Villa B', created_by=self.owner)
        self.staff = User.objects.create_user('staff', password=PASSWORD)
        assign_villa_staff(user=self.staff, villa=self.villa, assigned_by=self.owner)
        category = ExpenseCategory.objects.create(name='Gas')
        from apps.expenses.services import create_expense
        self.foreign_expense = create_expense(villa=self.other_villa, category=category, amount=Decimal('999.00'), date=date(2026, 2, 1), created_by=self.owner)

    def test_staff_full_day_inside_assigned_villa(self):
        self.assertTrue(self.client.login(username='staff', password=PASSWORD))
        partition_r = self.client.post(reverse('villas:partition_create', args=[self.villa.pk]), {'name': 'Unit 1'})
        partition = Partition.objects.get(villa=self.villa)
        self.assertRedirects(partition_r, reverse('villas:partition_detail', args=[partition.pk]))
        self.client.post(reverse('tenancy:create', args=[partition.pk]), {'name': 'Tenant X', 'move_in_date': '2026-01-01', 'monthly_rent': '1000.00'})
        rent = ChargeType.objects.create(name='Rent')
        self.client.post(reverse('billing:charge_create', args=[partition.pk]), {'charge_type': rent.pk, 'amount': '1000.00', 'frequency': 'monthly', 'start_date': '2026-01-01'})
        self.client.post(reverse('billing:invoice_generate', args=[partition.pk]), {'billing_period_start': '2026-02-01', 'billing_period_end': '2026-02-28', 'issue_date': '2026-02-01', 'due_date': '2026-02-10'})
        invoice = Invoice.objects.get(partition=partition)

        r = self.client.post(reverse('expenses:create_for_villa', args=[self.villa.pk]), {'villa': self.villa.pk, 'partition': partition.pk, 'name': 'Cleaner salary', 'amount': '300.00', 'date': '2026-02-03', 'due_date': '2026-02-28'})
        self.assertRedirects(r, reverse('villas:villa_detail', args=[self.villa.pk]))
        self.assertEqual(Expense.objects.get(villa=self.villa).created_by, self.staff)

        r = self.client.post(reverse('billing:payment_record', args=[invoice.pk]), {'amount': '1000.00', 'method': 'cash', 'collected_at': '2026-02-05'})
        self.assertRedirects(r, reverse('billing:invoice_detail', args=[invoice.pk]))
        payment = Payment.objects.get(invoice=invoice)
        r = self.client.post(reverse('cash:handover_submit'), {'payments': [payment.pk]})
        handover = CashHandover.objects.get(staff=self.staff)
        self.assertRedirects(r, reverse('cash:handover_detail', args=[handover.pk]))

        r = self.client.get(reverse('villas:villa_detail', args=[self.villa.pk]), {'period': 'month', 'year': 2026, 'month': 2})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('pnl', r.context)  # staff never see profit/loss
        self.assertNotContains(r, 'Financial summary')
        self.assertNotContains(r, 'Activity')
        self.assertContains(r, 'Cleaner salary')

    def test_staff_cannot_reach_or_leak_another_villas_data(self):
        self.client.login(username='staff', password=PASSWORD)
        self.assertEqual(self.client.get(reverse('villas:villa_detail', args=[self.other_villa.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('expenses:create_for_villa', args=[self.other_villa.pk])).status_code, 404)
        r = self.client.post(reverse('expenses:create'), {'villa': self.other_villa.pk, 'name': 'Gas', 'amount': '5.00', 'date': '2026-02-01'})
        self.assertEqual(r.status_code, 200)
        self.assertIn('villa', r.context['form'].errors)
        self.assertEqual(Expense.objects.filter(villa=self.other_villa).count(), 1)
        self.assertEqual(self.client.get(reverse('expenses:mark_paid', args=[self.foreign_expense.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('expenses:mark_paid', args=[self.foreign_expense.pk]), {'paid_on': '2026-02-02', 'payment_method': 'cash'}).status_code, 404)
        self.assertNotContains(self.client.get(reverse('expenses:list')), 'QAR 999.00')
        self.assertEqual(self.client.get(reverse('expenses:suggest'), {'name': 'Gas'}).json(), {'amount': None})

    def test_staff_cannot_cancel_or_reverse_expenses(self):
        self.client.login(username='staff', password=PASSWORD)
        from apps.expenses.services import create_expense
        own = create_expense(villa=self.villa, category=ExpenseCategory.objects.get(name='Gas'), amount=Decimal('10.00'), date=date(2026, 2, 1), created_by=self.staff)
        self.assertEqual(self.client.post(reverse('expenses:cancel', args=[own.pk]), {'reason': 'x'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('expenses:reverse', args=[own.pk]), {'reason': 'x'}).status_code, 403)
        own.refresh_from_db()
        self.assertEqual(own.status, Expense.Status.ACTIVE)

    def test_anonymous_users_are_sent_to_login(self):
        for url in (reverse('expenses:list'), reverse('expenses:create'), reverse('villas:villa_detail', args=[self.villa.pk]), reverse('expenses:suggest')):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn(reverse('accounts:login'), r['Location'])


class QueryCountTests(TestCase):
    """Law 13: page query counts must not grow with the number of rows shown."""

    def _populate(self, villa, owner, n, offset):
        from apps.expenses.services import create_expense, get_or_create_category
        from apps.tenancy.services import create_tenant
        from apps.villas.services import create_partition
        for i in range(n):
            partition = create_partition(villa=villa, name=f'P{offset + i}', created_by=owner)
            create_tenant(partition=partition, name=f'T{offset + i}', move_in_date=date(2026, 1, 1), monthly_rent=Decimal('100.00'), created_by=owner)
            create_expense(villa=villa, category=get_or_create_category(name=f'E{offset + i}'), amount=Decimal('10.00'), date=date(2026, 2, 1), due_date=date(2026, 2, 5), created_by=owner)

    def _count(self, url):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(ctx)

    def test_villa_detail_and_expense_list_query_counts_are_constant(self):
        owner = User.objects.create_user('owner', password=PASSWORD, is_owner=True)
        villa = create_villa(business=create_business(name='B', created_by=owner), name='V', created_by=owner)
        self.client.login(username='owner', password=PASSWORD)
        self._populate(villa, owner, 2, 0)
        self._count(reverse('villas:villa_detail', args=[villa.pk]))  # warm caches (content types etc.)
        few = (self._count(reverse('villas:villa_detail', args=[villa.pk])), self._count(reverse('expenses:list')))
        self._populate(villa, owner, 6, 10)
        many = (self._count(reverse('villas:villa_detail', args=[villa.pk])), self._count(reverse('expenses:list')))
        self.assertEqual(few, many)


PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32

class VillaListPeriodAndPhotoTests(TestCase):

    def setUp(self):
        from apps.billing.services import create_charge, generate_monthly_invoice, record_payment, get_or_create_charge_type
        from apps.expenses.services import create_expense, get_or_create_category
        from apps.tenancy.services import create_tenant
        from apps.accounts.services import assign_business_manager
        from apps.villas.services import create_partition
        self.owner = User.objects.create_user('owner', password=PASSWORD, is_owner=True)
        self.manager = User.objects.create_user('manager', password=PASSWORD)
        self.staff = User.objects.create_user('staff', password=PASSWORD)
        self.business = create_business(name='A', created_by=self.owner)
        self.other_business = create_business(name='B', created_by=self.owner)
        self.villa = create_villa(business=self.business, name='Villa A', created_by=self.owner)
        self.other_villa = create_villa(business=self.other_business, name='Villa B', created_by=self.owner)
        assign_business_manager(user=self.manager, business=self.business, assigned_by=self.owner)
        assign_villa_staff(user=self.staff, villa=self.villa, assigned_by=self.owner)
        self.partition = create_partition(villa=self.villa, name='Unit 1', created_by=self.owner)
        other_partition = create_partition(villa=self.other_villa, name='Unit B1', created_by=self.owner)
        for partition, rent in ((self.partition, '3000.00'), (other_partition, '1000.00')):
            create_tenant(partition=partition, name=f'T {partition.name}', move_in_date=date(2026, 1, 1), monthly_rent=Decimal(rent), created_by=self.owner)
            create_charge(partition=partition, charge_type=get_or_create_charge_type(name='Rent'), amount=Decimal(rent), start_date=date(2026, 1, 1), created_by=self.owner)
            invoice = generate_monthly_invoice(partition=partition, billing_period_start=date(2026, 2, 1), billing_period_end=date(2026, 2, 28), issue_date=date(2026, 2, 1), due_date=date(2026, 12, 31), generated_by=self.owner)
            record_payment(invoice=invoice, amount=Decimal(rent), method='cash', collected_by=self.owner, collected_at=date(2026, 2, 5), created_by=self.owner)
        create_expense(villa=self.villa, partition=self.partition, category=get_or_create_category(name='Wifi'), amount=Decimal('500.00'), date=date(2026, 2, 6), created_by=self.owner)
        create_expense(villa=self.villa, category=get_or_create_category(name='Water'), amount=Decimal('200.00'), date=date(2026, 2, 7), created_by=self.owner)

    def test_owner_sees_income_expense_profit_per_villa_for_the_chosen_period(self):
        self.client.login(username='owner', password=PASSWORD)
        r = self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': 2026, 'month': 2})
        rows = {v.name: v.pnl for v in r.context['page_obj']}
        self.assertEqual(rows['Villa A'], {'income': Decimal('3000.00'), 'expenses': Decimal('700.00'), 'net': Decimal('2300.00')})
        self.assertEqual(rows['Villa B'], {'income': Decimal('1000.00'), 'expenses': Decimal('0.00'), 'net': Decimal('1000.00')})
        self.assertEqual(r.context['totals']['net'], Decimal('3300.00'))
        r = self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': 2026, 'month': 3})
        self.assertTrue(all(v.pnl['net'] == 0 for v in r.context['page_obj']))
        r = self.client.get(reverse('villas:villa_list'), {'period': 'year', 'year': 2026})
        self.assertEqual(r.context['totals']['net'], Decimal('3300.00'))

    def test_manager_sees_only_own_business_figures_and_staff_see_none(self):
        self.client.login(username='manager', password=PASSWORD)
        r = self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': 2026, 'month': 2})
        self.assertEqual([v.name for v in r.context['page_obj']], ['Villa A'])
        self.assertEqual(r.context['totals']['income'], Decimal('3000.00'))
        self.client.logout()
        self.client.login(username='staff', password=PASSWORD)
        r = self.client.get(reverse('villas:villa_list'), {'period': 'month', 'year': 2026, 'month': 2})
        self.assertIsNone(r.context['totals'])
        self.assertTrue(all(v.pnl is None for v in r.context['page_obj']))
        self.assertNotContains(r, 'Income')

    def test_partition_page_has_its_own_profit_and_loss_and_expense_shortcut(self):
        self.client.login(username='owner', password=PASSWORD)
        r = self.client.get(reverse('villas:partition_detail', args=[self.partition.pk]), {'period': 'month', 'year': 2026, 'month': 2})
        self.assertEqual((r.context['pnl']['income'], r.context['pnl']['expenses'], r.context['pnl']['net']), (Decimal('3000.00'), Decimal('500.00'), Decimal('2500.00')))
        form = self.client.get(reverse('expenses:create'), {'partition': self.partition.pk}).context['form']
        self.assertEqual((form.initial['villa'], form.initial['partition']), (self.villa.pk, self.partition.pk))
        self.client.logout()
        self.client.login(username='staff', password=PASSWORD)
        self.assertNotIn('pnl', self.client.get(reverse('villas:partition_detail', args=[self.partition.pk])).context)

    def test_sidebar_no_longer_lists_partitions_or_tenants(self):
        self.client.login(username='owner', password=PASSWORD)
        r = self.client.get(reverse('dashboard:home'))
        self.assertNotContains(r, 'href="/partitions/"')
        self.assertNotContains(r, 'href="/tenants/"')

    def test_photo_upload_serve_scope_and_delete_rules(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.villas.models import Photo
        import shutil, tempfile
        from django.test import override_settings
        media = tempfile.mkdtemp()
        try:
            with override_settings(MEDIA_ROOT=media):
                self.client.login(username='staff', password=PASSWORD)
                r = self.client.post(reverse('villas:villa_photo_add', args=[self.villa.pk]), {'image': SimpleUploadedFile('front.png', PNG, content_type='image/png'), 'caption': 'Front'})
                self.assertRedirects(r, reverse('villas:villa_detail', args=[self.villa.pk]))
                r = self.client.post(reverse('villas:partition_photo_add', args=[self.partition.pk]), {'image': SimpleUploadedFile('room.png', PNG, content_type='image/png')})
                self.assertRedirects(r, reverse('villas:partition_detail', args=[self.partition.pk]))
                villa_photo = Photo.objects.get(partition__isnull=True)
                self.assertEqual(Photo.objects.count(), 2)
                self.assertTrue(AuditLog.objects.filter(action=Action.DOCUMENT_UPLOADED).count() == 2)
                self.assertEqual(self.client.get(reverse('villas:photo_file', args=[villa_photo.pk])).status_code, 200)
                # Disguised / oversized / wrong-type files are rejected and nothing is stored.
                for bad in (SimpleUploadedFile('evil.png', b'<html>not an image</html>', content_type='image/png'), SimpleUploadedFile('notes.txt', PNG, content_type='text/plain'), SimpleUploadedFile('big.png', PNG + b'\x00' * (5 * 1024 * 1024), content_type='image/png')):
                    r = self.client.post(reverse('villas:villa_photo_add', args=[self.villa.pk]), {'image': bad})
                    self.assertEqual(r.status_code, 200)
                    self.assertIn('image', r.context['form'].errors)
                self.assertEqual(Photo.objects.count(), 2)
                # Staff cannot delete; staff of another villa cannot even fetch it.
                self.assertEqual(self.client.post(reverse('villas:photo_delete', args=[villa_photo.pk]), {'reason': 'x'}).status_code, 403)
                self.client.logout()
                outsider = User.objects.create_user('outsider', password=PASSWORD)
                assign_villa_staff(user=outsider, villa=self.other_villa, assigned_by=self.owner)
                self.client.login(username='outsider', password=PASSWORD)
                self.assertEqual(self.client.get(reverse('villas:photo_file', args=[villa_photo.pk])).status_code, 404)
                self.assertEqual(self.client.post(reverse('villas:villa_photo_add', args=[self.villa.pk]), {'image': SimpleUploadedFile('a.png', PNG)}).status_code, 404)
                self.client.logout()
                self.assertEqual(self.client.get(reverse('villas:photo_file', args=[villa_photo.pk])).status_code, 302)
                self.client.login(username='owner', password=PASSWORD)
                r = self.client.post(reverse('villas:photo_delete', args=[villa_photo.pk]), {'reason': 'blurry'})
                self.assertRedirects(r, reverse('villas:villa_detail', args=[self.villa.pk]))
                self.assertEqual(Photo.objects.count(), 1)
                self.assertTrue(AuditLog.objects.filter(action=Action.DOCUMENT_DELETED, reason='blurry').exists())
        finally:
            shutil.rmtree(media, ignore_errors=True)
