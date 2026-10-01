from django.conf import settings
from django.db import models

class Notification(models.Model):

    class Kind(models.TextChoices):
        EXPENSE_DUE_SOON = ('expense_due_soon', 'Expense due soon')
        EXPENSE_OVERDUE = ('expense_overdue', 'Expense overdue')
        INVOICE_OVERDUE = ('invoice_overdue', 'Invoice overdue')
        CASH_HANDOVER_PENDING = ('cash_handover_pending', 'Cash handover pending')
        CASH_HANDOVER_REJECTED = ('cash_handover_rejected', 'Cash handover rejected')
        CONTRACT_EXPIRING = ('contract_expiring', 'Contract expiring')
        VACANT_PARTITION = ('vacant_partition', 'Vacant partition')
        COLLECTION_RECORDED = ('collection_recorded', 'Collection recorded')

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    kind = models.CharField(max_length=40, choices=Kind.choices)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=300, blank=True)
    url = models.CharField(max_length=300)
    dedupe_key = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [models.UniqueConstraint(fields=['recipient', 'dedupe_key'], name='unique_notification_per_recipient_event')]
        indexes = [models.Index(fields=['recipient', 'read_at', 'resolved_at'])]

    def __str__(self):
        return f'{self.get_kind_display()} → {self.recipient}'
