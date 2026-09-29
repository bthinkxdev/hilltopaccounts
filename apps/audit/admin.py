from django.contrib import admin
from .models import AuditLog

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'user', 'action', 'content_type', 'object_id', 'business', 'villa')
    list_filter = ('action', 'business', 'villa')
    search_fields = ('user__username', 'object_id', 'reason')
    date_hierarchy = 'timestamp'
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
