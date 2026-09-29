import secrets
from django.conf import settings
from django.db import models

def _generate_invoice_number() -> str:
    return f'INV-{secrets.token_hex(5).upper()}'

class ChargeType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

class Charge(models.Model):

    class Frequency(models.TextChoices):
        MONTHLY = ('monthly', 'Monthly')
        ONE_TIME = ('one_time', 'One-time')
    partition = models.ForeignKey('villas.Partition', on_delete=models.PROTECT, related_name='charges')
    charge_type = models.ForeignKey(ChargeType, on_delete=models.PROTECT, related_name='charges')
    description = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    frequency = models.CharField(max_length=20, choices=Frequency.choices, default=Frequency.MONTHLY)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True)

    class Meta:
        ordering = ['partition__villa__name', 'partition__name', 'charge_type__name']
        constraints = [models.CheckConstraint(check=models.Q(amount__gte=0), name='charge_amount_non_negative'), models.CheckConstraint(check=models.Q(end_date__isnull=True) | models.Q(end_date__gte=models.F('start_date')), name='charge_end_date_after_start_date')]

    def __str__(self):
        return f'{self.charge_type} — {self.partition} ({self.amount})'

class Invoice(models.Model):
    partition = models.ForeignKey('villas.Partition', on_delete=models.PROTECT, related_name='invoices')
    tenant = models.ForeignKey('tenancy.Tenant', on_delete=models.PROTECT, related_name='invoices')
    invoice_number = models.CharField(max_length=32, unique=True, default=_generate_invoice_number, editable=False)
    billing_period_start = models.DateField()
    billing_period_end = models.DateField()
    issue_date = models.DateField()
    due_date = models.DateField()
    is_cancelled = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True)

    class Status(models.TextChoices):
        PENDING = ('pending', 'Pending')
        PARTIALLY_PAID = ('partially_paid', 'Partially Paid')
        PAID = ('paid', 'Paid')
        OVERDUE = ('overdue', 'Overdue')
        CANCELLED = ('cancelled', 'Cancelled')

    class Meta:
        ordering = ['-issue_date', '-id']
        constraints = [models.UniqueConstraint(fields=['partition', 'billing_period_start', 'billing_period_end'], name='unique_invoice_per_partition_per_period'), models.CheckConstraint(check=models.Q(due_date__gte=models.F('issue_date')), name='invoice_due_date_after_issue_date')]

    @property
    def business(self):
        return self.partition.villa.business

    @property
    def villa(self):
        return self.partition.villa

    @property
    def total(self):
        from . import selectors
        return selectors.invoice_total(self)

    @property
    def paid_amount(self):
        from . import selectors
        return selectors.invoice_paid_amount(self)

    @property
    def outstanding(self):
        from . import selectors
        return selectors.invoice_outstanding(self)

    @property
    def status(self):
        from . import selectors
        return selectors.invoice_status(self)

    def __str__(self):
        return self.invoice_number

class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    charge = models.ForeignKey(Charge, on_delete=models.PROTECT, related_name='invoice_items', null=True, blank=True)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        constraints = [models.CheckConstraint(check=models.Q(amount__gte=0), name='invoice_item_amount_non_negative')]

    def __str__(self):
        return f'{self.description}: {self.amount}'

class Payment(models.Model):

    class Method(models.TextChoices):
        CASH = ('cash', 'Cash')
        BANK_TRANSFER = ('bank_transfer', 'Bank Transfer')
        OTHER = ('other', 'Other')
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='payments')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices)
    collected_at = models.DateField()
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    collected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='collections')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    is_cancelled = models.BooleanField(default=False)
    cancelled_reason = models.TextField(blank=True)
    corrects = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='corrections')

    class Meta:
        ordering = ['-collected_at', '-id']
        constraints = [models.CheckConstraint(check=models.Q(amount__gt=0), name='payment_amount_positive')]

    def __str__(self):
        return f'{self.amount} — {self.invoice.invoice_number}'
