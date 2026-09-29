from django.contrib import admin
from .models import Business
from .services import create_business, update_business

@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_archived', 'created_at', 'created_by')
    list_filter = ('is_archived',)
    search_fields = ('name',)
    readonly_fields = ('created_at', 'created_by')

    def save_model(self, request, obj, form, change):
        if change:
            update_business(business=obj, name=obj.name, description=obj.description, updated_by=request.user)
        else:
            created = create_business(name=obj.name, description=obj.description, created_by=request.user)
            obj.pk = created.pk

    def has_delete_permission(self, request, obj=None):
        return False
