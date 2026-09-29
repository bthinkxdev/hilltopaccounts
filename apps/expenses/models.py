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
    payment_method = models.CharField(max_length=20, choices=Method.choices)
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
        constraints = [models.CheckConstraint(check=models.Q(amount__gt=0), name='expense_amount_positive')]

    def clean(self):
        super().clean()
        if self.villa_id and self.villa.business_id != self.business_id:
            raise ValidationError('The selected villa does not belong to the selected business.')
        if self.partition_id and self.villa_id and (self.partition.villa_id != self.villa_id):
            raise ValidationError('The selected partition does not belong to the selected villa.')

    def __str__(self):
        return f'{self.category} — {self.amount} ({self.date})'
