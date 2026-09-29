from django import forms
from django.contrib import admin
from .models import Tenant
from .services import create_tenant, update_tenant

class TenantAdminForm(forms.ModelForm):

    class Meta:
        model = Tenant
        fields = '__all__'

    def clean(self):
        cleaned = super().clean()
        partition = cleaned.get('partition')
        if partition and (not self.instance.pk) and partition.is_occupied:
            raise forms.ValidationError(f'{partition} already has an active tenant.')
        return cleaned

@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    form = TenantAdminForm
    list_display = ('name', 'partition', 'status', 'move_in_date', 'move_out_date', 'monthly_rent')
    list_filter = ('partition__villa__business', 'partition__villa', 'status')
    search_fields = ('name', 'mobile', 'id_document_number', 'partition__name')
    autocomplete_fields = ['partition']
    readonly_fields = ('created_at', 'created_by', 'updated_at', 'updated_by')

    def save_model(self, request, obj, form, change):
        if change:
            update_tenant(tenant=obj, updated_by=request.user, name=obj.name, mobile=obj.mobile, id_document_number=obj.id_document_number, nationality=obj.nationality, move_in_date=obj.move_in_date, move_out_date=obj.move_out_date, monthly_rent=obj.monthly_rent, deposit=obj.deposit, status=obj.status, notes=obj.notes)
        else:
            created = create_tenant(partition=obj.partition, name=obj.name, move_in_date=obj.move_in_date, monthly_rent=obj.monthly_rent, created_by=request.user, mobile=obj.mobile, id_document_number=obj.id_document_number, nationality=obj.nationality, deposit=obj.deposit, notes=obj.notes)
            obj.pk = created.pk

    def has_delete_permission(self, request, obj=None):
        return False
