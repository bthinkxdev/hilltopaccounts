from django.conf import settings
from django.db import models

class CashHandover(models.Model):

    class Status(models.TextChoices):
        SUBMITTED = ('submitted', 'Submitted')
        CONFIRMED = ('confirmed', 'Confirmed')
        REJECTED = ('rejected', 'Rejected')
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cash_handovers')
    payments = models.ManyToManyField('billing.Payment', related_name='handovers', blank=True)
    declared_amount = models.DecimalField(max_digits=12, decimal_places=2)
    confirmed_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)
    submitted_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    notes = models.TextField(blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-submitted_at']
        constraints = [models.CheckConstraint(check=models.Q(declared_amount__gt=0), name='handover_declared_amount_positive')]

    @property
    def discrepancy(self):
        if self.confirmed_amount is None:
            return None
        return self.declared_amount - self.confirmed_amount

    def __str__(self):
        return f'Handover #{self.pk} — {self.staff} — {self.declared_amount}'
