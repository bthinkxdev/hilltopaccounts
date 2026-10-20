from decimal import Decimal
from django.conf import settings
from django.db import models

class Tenant(models.Model):

    class Status(models.TextChoices):
        ACTIVE = ('active', 'Active')
        MOVED_OUT = ('moved_out', 'Moved Out')
        ARCHIVED = ('archived', 'Archived')

    class DepositStatus(models.TextChoices):
        RECEIVED = ('received', 'Received')
        PENDING = ('pending', 'Pending')
        PARTIALLY_PAID = ('partially_paid', 'Partially Paid')

    partition = models.ForeignKey('villas.Partition', on_delete=models.PROTECT, related_name='tenancies')
    name = models.CharField(max_length=200)
    mobile = models.CharField(max_length=32, blank=True)
    id_document_number = models.CharField(max_length=64, blank=True)
    nationality = models.CharField(max_length=100, blank=True)
    move_in_date = models.DateField()
    move_out_date = models.DateField(null=True, blank=True)
    monthly_rent = models.DecimalField(max_digits=12, decimal_places=2)
    deposit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    deposit_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    deposit_status = models.CharField(max_length=20, choices=DepositStatus.choices, default=DepositStatus.PENDING)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True)

    class Meta:
        ordering = ['-move_in_date']
        constraints = [models.UniqueConstraint(fields=['partition'], condition=models.Q(status='active'), name='unique_active_tenant_per_partition'), models.CheckConstraint(check=models.Q(monthly_rent__gte=0) & models.Q(deposit__gte=0), name='tenant_amounts_non_negative')]

    @property
    def deposit_pending(self):
        return max(Decimal('0.00'), self.deposit - self.deposit_paid)

    def sync_deposit_status(self):
        if self.deposit is None:
            self.deposit = Decimal('0.00')
        if self.deposit_paid is None:
            self.deposit_paid = Decimal('0.00')
        if self.deposit > 0 and self.deposit_paid >= self.deposit:
            self.deposit_status = self.DepositStatus.RECEIVED
        elif self.deposit_paid > 0:
            self.deposit_status = self.DepositStatus.PARTIALLY_PAID
        else:
            self.deposit_status = self.DepositStatus.PENDING

    def clean(self):
        self.sync_deposit_status()
        super().clean()

    def save(self, *args, **kwargs):
        self.sync_deposit_status()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.name} — {self.partition}'
