from django.contrib import admin
from .models import CashHandover

@admin.register(CashHandover)
class CashHandoverAdmin(admin.ModelAdmin):
    list_display = ('id', 'staff', 'declared_amount', 'confirmed_amount', 'status', 'submitted_at')
    list_filter = ('status',)
    search_fields = ('staff__username',)
    readonly_fields = [f.name for f in CashHandover._meta.fields if f.name != 'payments']
    filter_horizontal = ('payments',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
