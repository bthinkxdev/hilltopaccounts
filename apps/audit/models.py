from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

class Action(models.TextChoices):
    AUTH_LOGIN = ('auth.login', 'Login')
    AUTH_LOGOUT = ('auth.logout', 'Logout')
    AUTH_FAILED_LOGIN = ('auth.failed_login', 'Failed login')
    PASSWORD_CHANGED = ('auth.password_changed', 'Password changed')
    USER_CREATED = ('user.created', 'User created')
    USER_UPDATED = ('user.updated', 'User updated')
    USER_DISABLED = ('user.disabled', 'User disabled')
    ROLE_CHANGED = ('user.role_changed', 'Role changed')
    BUSINESS_ASSIGNMENT_CHANGED = ('user.business_assignment_changed', 'Business assignment changed')
    VILLA_ASSIGNMENT_CHANGED = ('user.villa_assignment_changed', 'Villa assignment changed')
    BUSINESS_CREATED = ('business.created', 'Business created')
    BUSINESS_UPDATED = ('business.updated', 'Business updated')
    BUSINESS_ARCHIVED = ('business.archived', 'Business archived')
    VILLA_CREATED = ('villa.created', 'Villa created')
    VILLA_UPDATED = ('villa.updated', 'Villa updated')
    VILLA_ARCHIVED = ('villa.archived', 'Villa archived')
    PARTITION_CREATED = ('partition.created', 'Partition created')
    PARTITION_UPDATED = ('partition.updated', 'Partition updated')
    PARTITION_ARCHIVED = ('partition.archived', 'Partition archived')
    TENANT_CREATED = ('tenant.created', 'Tenant created')
    TENANT_UPDATED = ('tenant.updated', 'Tenant updated')
    TENANT_MOVED_IN = ('tenant.moved_in', 'Tenant moved in')
    TENANT_MOVED_OUT = ('tenant.moved_out', 'Tenant moved out')
    CHARGE_CREATED = ('charge.created', 'Charge created')
    CHARGE_UPDATED = ('charge.updated', 'Charge updated')
    CHARGE_CANCELLED = ('charge.cancelled', 'Charge cancelled')
    INVOICE_CREATED = ('invoice.created', 'Invoice created')
    INVOICE_UPDATED = ('invoice.updated', 'Invoice updated')
    INVOICE_CANCELLED = ('invoice.cancelled', 'Invoice cancelled')
    COLLECTION_CREATED = ('collection.created', 'Collection created')
    COLLECTION_UPDATED = ('collection.updated', 'Collection updated')
    COLLECTION_CANCELLED = ('collection.cancelled', 'Collection cancelled')
    EXPENSE_CREATED = ('expense.created', 'Expense created')
    EXPENSE_UPDATED = ('expense.updated', 'Expense updated')
    EXPENSE_CANCELLED = ('expense.cancelled', 'Expense cancelled')
    CASH_HANDOVER_CREATED = ('cash_handover.created', 'Cash handover created')
    CASH_HANDOVER_SUBMITTED = ('cash_handover.submitted', 'Cash handover submitted')
    CASH_HANDOVER_CONFIRMED = ('cash_handover.confirmed', 'Cash handover confirmed')
    CASH_HANDOVER_REJECTED = ('cash_handover.rejected', 'Cash handover rejected')
    REPORT_VIEWED = ('report.viewed', 'Report viewed')
    REPORT_EXPORTED = ('report.exported', 'Report exported')
    DOCUMENT_UPLOADED = ('document.uploaded', 'Document uploaded')
    DOCUMENT_DOWNLOADED = ('document.downloaded', 'Document downloaded')
    DOCUMENT_DELETED = ('document.deleted', 'Document deleted')

class AuditLog(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='audit_logs')
    action = models.CharField(max_length=64, choices=Action.choices, db_index=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, null=True, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    content_object = GenericForeignKey('content_type', 'object_id')
    business = models.ForeignKey('businesses.Business', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    villa = models.ForeignKey('villas.Villa', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    request_id = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [models.Index(fields=['content_type', 'object_id']), models.Index(fields=['action', 'timestamp'])]

    def __str__(self):
        return f'{self.get_action_display()} by {self.user} at {self.timestamp:%Y-%m-%d %H:%M}'
