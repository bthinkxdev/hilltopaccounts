from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

class ExpenseCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'expense categories'

    def __str__(self):
        return self.name

class PaidBy(models.TextChoices):
    STAFF = ('staff', 'Staff — from collected cash')
    OWNER = ('owner', 'Owner — own account')

class RecurringExpense(models.Model):
    """A fixed monthly expense of one villa (owner rent, electricity, Wi-Fi, maintenance, villa service…).

    Each month it produces one ordinary Expense, so all totals, due dates and audit keep flowing through one engine.
    """
    villa = models.ForeignKey('villas.Villa', on_delete=models.PROTECT, related_name='recurring_expenses')
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='recurring_expenses')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    due_day = models.PositiveSmallIntegerField(default=1, help_text='Day of the month it falls due (1–28).')
    paid_by = models.CharField(max_length=10, choices=PaidBy.choices, default=PaidBy.OWNER)
    auto_debit = models.BooleanField(default=False, help_text='Debited automatically from the owner account on the due date.')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True)

    class Meta:
        ordering = ['villa__name', 'category__name']
        constraints = [
            models.UniqueConstraint(fields=['villa', 'category'], name='unique_recurring_expense_per_villa_and_name'),
            models.CheckConstraint(check=models.Q(amount__gt=0), name='recurring_expense_amount_positive'),
            models.CheckConstraint(check=models.Q(due_day__gte=1, due_day__lte=28), name='recurring_expense_due_day_1_to_28'),
            models.CheckConstraint(check=models.Q(auto_debit=False) | models.Q(paid_by='owner'), name='recurring_auto_debit_is_owner_paid'),
        ]

    def __str__(self):
        return f'{self.category} — {self.villa.name} ({self.amount}/mo)'

class Expense(models.Model):

    class Status(models.TextChoices):
        ACTIVE = ('active', 'Active')
        CANCELLED = ('cancelled', 'Cancelled')
        REVERSED = ('reversed', 'Reversed')

    class Method(models.TextChoices):
        CASH = ('cash', 'Cash')
        BANK_TRANSFER = ('bank_transfer', 'Bank Transfer')
        OTHER = ('other', 'Other')
    business = models.ForeignKey('businesses.Business', on_delete=models.PROTECT, related_name='expenses')
    villa = models.ForeignKey('villas.Villa', on_delete=models.PROTECT, related_name='expenses', null=True, blank=True)
    partition = models.ForeignKey('villas.Partition', on_delete=models.PROTECT, related_name='expenses', null=True, blank=True)
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='expenses')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    paid_on = models.DateField(null=True, blank=True)
    payment_method = models.CharField(max_length=20, choices=Method.choices, blank=True)
    paid_by = models.CharField(max_length=10, choices=PaidBy.choices, default=PaidBy.OWNER)
    paid_by_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True, help_text='When the Owner / Business Manager confirmed the payment as received/valid.')
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    recurring = models.ForeignKey(RecurringExpense, on_delete=models.PROTECT, related_name='expenses', null=True, blank=True)
    period = models.DateField(null=True, blank=True, help_text='First day of the month a recurring expense belongs to.')
    description = models.TextField(blank=True)
    reference = models.CharField(max_length=100, blank=True)
    attachment = models.FileField(upload_to='expense_attachments/%Y/%m/', null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    status_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True)

    class Meta:
        ordering = ['-date', '-id']
        constraints = [models.CheckConstraint(check=models.Q(amount__gt=0), name='expense_amount_positive'), models.CheckConstraint(check=models.Q(paid_on__isnull=True) | ~models.Q(payment_method=''), name='expense_paid_has_method'), models.UniqueConstraint(fields=['recurring', 'period'], condition=models.Q(recurring__isnull=False), name='one_expense_per_recurring_per_month'), models.CheckConstraint(check=models.Q(recurring__isnull=True) | models.Q(period__isnull=False), name='recurring_expense_has_period'), models.CheckConstraint(check=models.Q(verified_at__isnull=True) | models.Q(paid_on__isnull=False), name='expense_verified_only_when_paid')]

    @property
    def is_paid(self) -> bool:
        return self.paid_on is not None

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None

    @property
    def awaiting_verification(self) -> bool:
        return self.status == self.Status.ACTIVE and self.paid_on is not None and self.verified_at is None

    def clean(self):
        super().clean()
        if self.paid_on and not self.payment_method:
            raise ValidationError('A payment method is required once an expense is marked paid.')
        if self.villa_id and self.villa.business_id != self.business_id:
            raise ValidationError('The selected villa does not belong to the selected business.')
        if self.partition_id and self.villa_id and (self.partition.villa_id != self.villa_id):
            raise ValidationError('The selected partition does not belong to the selected villa.')

    def __str__(self):
        return f'{self.category} — {self.amount} ({self.date})'
