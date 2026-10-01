import calendar
from datetime import date, timedelta
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.accounts.models import Assignment, User
from apps.accounts.services import assign_accountant, assign_business_manager, assign_villa_staff, create_user
from apps.billing import selectors as billing_selectors
from apps.billing.exceptions import DuplicateInvoicePeriod, PartitionVacant
from apps.billing.models import ChargeType, Invoice
from apps.billing.services import create_charge, generate_monthly_invoice, record_payment
from apps.businesses.models import Business
from apps.businesses.services import create_business
from apps.cash import selectors as cash_selectors
from apps.cash.services import confirm_cash_handover, submit_cash_handover
from apps.expenses.models import Expense, ExpenseCategory
from apps.expenses.services import create_expense
from apps.tenancy.models import Tenant
from apps.tenancy.services import create_tenant
from apps.villas.models import Villa
from apps.villas.services import create_partition, create_villa
DEMO_PASSWORD = 'Passw0rd!2026'
CHARGE_TYPE_NAMES = ['Rent', 'Electricity', 'Water', 'Wi-Fi', 'Cleaning', 'Maintenance', 'Gas', 'Parking']
EXPENSE_CATEGORY_NAMES = ['Maintenance', 'Cleaning', 'Utilities', 'Staff', 'Other']
TENANT_NAMES = ['Ahmed Al-Sayed', 'Fatima Hassan', 'Mohammed Rahman', 'Sara Khan', 'John Smith', 'Priya Patel', 'Youssef Ibrahim', 'Layla Mansour', 'David Okafor', 'Aisha Bello']

def _shift_months(d: date, delta: int) -> date:
    month_index = d.month - 1 + delta
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

class Command(BaseCommand):
    help = 'Populate the database with realistic demo data across every module (development use only).'

    def add_arguments(self, parser):
        parser.add_argument('--months', type=int, default=3, help='How many past billing months to generate invoices for (default 3).')

    def handle(self, *args, **options):
        months = options['months']
        with transaction.atomic():
            owner = self._get_or_create_owner()
            charge_types = self._get_or_create_charge_types()
            self._get_or_create_expense_categories()
            businesses = self._get_or_create_businesses(owner)
            villas = self._get_or_create_villas(owner, businesses)
            partitions = self._get_or_create_partitions(owner, villas)
            manager, villa_staff, accountant = self._get_or_create_staff(owner, businesses, villas)
            villa_to_collector = {villas[i].pk: villa_staff[i] for i in range(min(len(villa_staff), 2))}
            tenants = self._get_or_create_tenants(owner, partitions)
            self._get_or_create_charges(owner, partitions, tenants, charge_types)
            invoices = self._generate_invoices(owner, partitions, tenants, months)
            self._record_payments(owner, villa_to_collector, invoices)
            self._create_expenses(owner, businesses, villas)
            self._create_cash_handovers(owner, villa_staff)
        self.stdout.write(self.style.SUCCESS('\nDemo data ready. Sign in with any of:'))
        self.stdout.write(f'  Owner:            owner / {DEMO_PASSWORD}')
        self.stdout.write(f'  Business Manager: manager1 / {DEMO_PASSWORD}')
        self.stdout.write(f'  Villa Staff:      staff1 / {DEMO_PASSWORD}')
        self.stdout.write(f'  Villa Staff:      staff2 / {DEMO_PASSWORD}')
        self.stdout.write(f'  Accountant:       accountant1 / {DEMO_PASSWORD}')

    def _get_or_create_owner(self) -> User:
        owner = User.objects.filter(username='owner').first()
        if owner is None:
            owner = User.objects.create_superuser('owner', 'owner@example.com', DEMO_PASSWORD)
            owner.is_owner = True
            owner.first_name = 'Faisal'
            owner.last_name = 'Owner'
            owner.save()
            self.stdout.write('Created Owner account.')
        return owner

    def _get_or_create_charge_types(self) -> dict:
        return {name: ChargeType.objects.get_or_create(name=name)[0] for name in CHARGE_TYPE_NAMES}

    def _get_or_create_expense_categories(self):
        for name in EXPENSE_CATEGORY_NAMES:
            ExpenseCategory.objects.get_or_create(name=name)

    def _get_or_create_businesses(self, owner) -> list:
        businesses = []
        for name in ['Al Waab Properties', 'Pearl Residences']:
            business = Business.objects.filter(name=name).first()
            if business is None:
                business = create_business(name=name, description=f'Demo business — {name}', created_by=owner)
                self.stdout.write(f'Created business: {name}')
            businesses.append(business)
        return businesses

    def _get_or_create_villas(self, owner, businesses) -> list:
        specs = {businesses[0].name: ['Villa 12 — Al Waab', 'Villa 7 — Muaither'], businesses[1].name: ['Tower A — Pearl Qatar', 'Tower B — Pearl Qatar']}
        villas = []
        for business in businesses:
            for villa_name in specs[business.name]:
                villa = Villa.objects.filter(business=business, name=villa_name).first()
                if villa is None:
                    villa = create_villa(business=business, name=villa_name, address=f'{villa_name}, Doha, Qatar', landlord_name='Al Thani Holdings', created_by=owner)
                    self.stdout.write(f'Created villa: {villa_name}')
                villas.append(villa)
        return villas

    def _get_or_create_partitions(self, owner, villas) -> list:
        partitions = []
        for villa in villas:
            for i in range(1, 5):
                name = f'Unit {100 + i}'
                partition = villa.partitions.filter(name=name).first()
                if partition is None:
                    partition = create_partition(villa=villa, name=name, created_by=owner)
                partitions.append(partition)
        return partitions

    def _get_or_create_staff(self, owner, businesses, villas):
        manager = User.objects.filter(username='manager1').first()
        if manager is None:
            manager = create_user(username='manager1', first_name='Layla', last_name='Manager', created_by=owner, password=DEMO_PASSWORD)
        if not Assignment.objects.filter(user=manager, role=Assignment.Role.BUSINESS_MANAGER).exists():
            assign_business_manager(user=manager, business=businesses[0], assigned_by=owner)
        villa_staff = []
        for i, username in enumerate(['staff1', 'staff2']):
            staff = User.objects.filter(username=username).first()
            if staff is None:
                staff = create_user(username=username, first_name=f'Staff{i + 1}', last_name='Member', created_by=owner, password=DEMO_PASSWORD)
            if not Assignment.objects.filter(user=staff, role=Assignment.Role.VILLA_STAFF, villa=villas[i]).exists():
                assign_villa_staff(user=staff, villa=villas[i], assigned_by=owner)
            villa_staff.append(staff)
        accountant = User.objects.filter(username='accountant1').first()
        if accountant is None:
            accountant = create_user(username='accountant1', first_name='Omar', last_name='Accountant', created_by=owner, password=DEMO_PASSWORD)
        if not Assignment.objects.filter(user=accountant, role=Assignment.Role.ACCOUNTANT).exists():
            assign_accountant(user=accountant, business=businesses[0], assigned_by=owner)
        return (manager, villa_staff, accountant)

    def _get_or_create_tenants(self, owner, partitions) -> dict:
        tenants = {}
        move_in = _shift_months(date.today(), -6)
        for i, partition in enumerate(partitions):
            if i % 10 == 3:
                continue
            existing = partition.tenancies.filter(status=Tenant.Status.ACTIVE).first()
            if existing:
                tenants[partition.pk] = existing
                continue
            name = TENANT_NAMES[i % len(TENANT_NAMES)]
            rent = Decimal('2500.00') + Decimal(i % 5) * Decimal('500.00')
            tenant = create_tenant(partition=partition, name=name, mobile=f'+974 5{500000 + i:06d}', nationality='Qatar', move_in_date=move_in, monthly_rent=rent, deposit=rent, created_by=owner)
            tenants[partition.pk] = tenant
        return tenants

    def _get_or_create_charges(self, owner, partitions, tenants, charge_types):
        for partition in partitions:
            tenant = tenants.get(partition.pk)
            if tenant is None:
                continue
            if partition.charges.filter(charge_type=charge_types['Rent']).exists():
                continue
            start = tenant.move_in_date
            create_charge(partition=partition, charge_type=charge_types['Rent'], amount=tenant.monthly_rent, start_date=start, created_by=owner)
            create_charge(partition=partition, charge_type=charge_types['Electricity'], amount=Decimal('150.00'), start_date=start, created_by=owner)
            create_charge(partition=partition, charge_type=charge_types['Water'], amount=Decimal('50.00'), start_date=start, created_by=owner)

    def _generate_invoices(self, owner, partitions, tenants, months) -> list:
        invoices = []
        this_month_start = date.today().replace(day=1)
        for partition in partitions:
            if tenants.get(partition.pk) is None:
                continue
            for offset in range(months, 0, -1):
                period_start = _shift_months(this_month_start, -offset)
                period_end = _shift_months(period_start, 1) - timedelta(days=1)
                due = period_start + timedelta(days=9)
                try:
                    invoice = generate_monthly_invoice(partition=partition, billing_period_start=period_start, billing_period_end=period_end, issue_date=period_start, due_date=due, generated_by=owner)
                except (DuplicateInvoicePeriod, PartitionVacant):
                    invoice = Invoice.objects.filter(partition=partition, billing_period_start=period_start, billing_period_end=period_end).first()
                if invoice:
                    invoices.append((invoice, offset))
        return invoices

    def _record_payments(self, owner, villa_to_collector, invoices):
        for invoice, months_ago in invoices:
            if invoice.payments.exists():
                continue
            total = billing_selectors.invoice_total(invoice)
            if total <= 0:
                continue
            collector = villa_to_collector.get(invoice.partition.villa_id, owner)
            if months_ago >= 3:
                record_payment(invoice=invoice, amount=total, method='bank_transfer', collected_by=owner, collected_at=invoice.due_date, created_by=owner)
            elif months_ago == 2:
                record_payment(invoice=invoice, amount=(total / 2).quantize(Decimal('0.01')), method='cash', collected_by=collector, collected_at=invoice.due_date, created_by=collector)

    def _create_expenses(self, owner, businesses, villas):
        maintenance = ExpenseCategory.objects.get(name='Maintenance')
        utilities = ExpenseCategory.objects.get(name='Utilities')
        today = date.today()
        specs = [(villas[0].business, villas[0], maintenance, Decimal('1200.00'), 'Annual AC servicing'), (villas[0].business, villas[0], utilities, Decimal('340.00'), 'Common area electricity'), (villas[1].business, villas[1], maintenance, Decimal('560.00'), 'Plumbing repair')]
        for business, villa, category, amount, description in specs:
            already_seeded = Expense.objects.filter(business=business, villa=villa, category=category, amount=amount).exists()
            if already_seeded:
                continue
            create_expense(villa=villa, category=category, amount=amount, date=today - timedelta(days=10), payment_method='bank_transfer', paid_on=today - timedelta(days=10), description=description, created_by=owner)

    def _create_cash_handovers(self, owner, villa_staff):
        if not villa_staff:
            return
        staff = villa_staff[0]
        unclaimed = cash_selectors.unclaimed_cash_payments(staff)
        if not unclaimed.exists():
            return
        handover = submit_cash_handover(staff=staff, payments=unclaimed, submitted_by=staff)
        confirmed_amount = max(handover.declared_amount - Decimal('50.00'), Decimal('0.00'))
        confirm_cash_handover(handover=handover, confirmed_amount=confirmed_amount, confirmed_by=owner)
