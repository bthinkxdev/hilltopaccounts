from django.contrib import admin
from .models import Partition, Villa
from .services import PARTITION_TRACKED_FIELDS, TRACKED_FIELDS, create_partition, create_villa, update_partition, update_villa

@admin.register(Villa)
class VillaAdmin(admin.ModelAdmin):
    list_display = ('name', 'business', 'is_archived', 'created_at', 'created_by')
    list_filter = ('business', 'is_archived')
    search_fields = ('name', 'business__name')
    autocomplete_fields = ['business']
    readonly_fields = ('created_at', 'created_by')

    def save_model(self, request, obj, form, change):
        field_values = {field: getattr(obj, field) for field in TRACKED_FIELDS}
        if change:
            update_villa(villa=obj, updated_by=request.user, **field_values)
        else:
            created = create_villa(business=obj.business, created_by=request.user, **field_values)
            obj.pk = created.pk

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Partition)
class PartitionAdmin(admin.ModelAdmin):
    list_display = ('name', 'villa', 'status', 'occupancy', 'created_at')
    list_filter = ('villa__business', 'villa', 'status')
    search_fields = ('name', 'villa__name')
    autocomplete_fields = ['villa']
    readonly_fields = ('created_at', 'created_by', 'updated_at', 'updated_by')

    @admin.display(description='Occupancy')
    def occupancy(self, obj):
        return 'Occupied' if obj.is_occupied else 'Vacant'

    def save_model(self, request, obj, form, change):
        field_values = {field: getattr(obj, field) for field in PARTITION_TRACKED_FIELDS}
        if change:
            update_partition(partition=obj, updated_by=request.user, **field_values)
        else:
            created = create_partition(villa=obj.villa, created_by=request.user, **field_values)
            obj.pk = created.pk

    def has_delete_permission(self, request, obj=None):
        return False
