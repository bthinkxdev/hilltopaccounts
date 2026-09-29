from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.core.exceptions import PermissionDenied
from apps.audit import services as audit_services
from apps.audit.models import Action
from .models import Assignment, User

@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (('ERP access', {'fields': ('is_owner', 'phone')}),)
    list_display = ('username', 'first_name', 'last_name', 'email', 'is_owner', 'is_active')
    list_filter = ('is_owner', 'is_active', 'is_superuser')
    search_fields = ('username', 'first_name', 'last_name', 'email')

@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'business', 'villa', 'created_at', 'created_by')
    list_filter = ('role',)
    autocomplete_fields = ['user', 'business', 'villa']
    readonly_fields = ['created_at', 'created_by']

    def save_model(self, request, obj, form, change):
        if change:
            raise PermissionDenied('Assignments are immutable — remove this one and create a new one instead.')
        obj.created_by = request.user
        super().save_model(request, obj, form, change)
        action = Action.VILLA_ASSIGNMENT_CHANGED if obj.villa_id else Action.BUSINESS_ASSIGNMENT_CHANGED
        audit_services.log(user=request.user, action=action, obj=obj, business=obj.business or (obj.villa.business if obj.villa_id else None), villa=obj.villa, new_value={'assigned_user': obj.user.username, 'role': obj.role})

    def delete_model(self, request, obj):
        from .services import remove_assignment
        remove_assignment(assignment=obj, removed_by=request.user, reason='removed via admin')

    def delete_queryset(self, request, queryset):
        from .services import remove_assignment
        for obj in queryset:
            remove_assignment(assignment=obj, removed_by=request.user, reason='bulk removed via admin')
