from django.contrib import admin
from . import selectors
from .models import Charge, ChargeType, Invoice, InvoiceItem, Payment
from .services import create_charge, update_charge

@admin.register(ChargeType)
class ChargeTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active')
    search_fields = ('name',)

@admin.register(Charge)
class ChargeAdmin(admin.ModelAdmin):
    list_display = ('charge_type', 'partition', 'amount', 'frequency', 'is_active')
    list_filter = ('partition__villa__business', 'charge_type', 'is_active')
    search_fields = ('partition__name', 'charge_type__name')
    autocomplete_fields = ['partition', 'charge_type']
    readonly_fields = ('created_at', 'created_by', 'updated_at', 'updated_by')

    def save_model(self, request, obj, form, change):
        field_values = dict(charge_type=obj.charge_type, description=obj.description, amount=obj.amount, frequency=obj.frequency, start_date=obj.start_date, end_date=obj.end_date, is_active=obj.is_active)
        if change:
            update_charge(charge=obj, updated_by=request.user, **field_values)
        else:
            created = create_charge(partition=obj.partition, created_by=request.user, **field_values)
            obj.pk = created.pk

    def has_delete_permission(self, request, obj=None):
        return False

class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0
    readonly_fields = ('charge', 'description', 'amount')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'partition', 'tenant', 'issue_date', 'due_date', 'computed_status', 'computed_outstanding')
    list_filter = ('partition__villa__business', 'partition__villa', 'is_cancelled')
    search_fields = ('invoice_number', 'tenant__name', 'partition__name')
    autocomplete_fields = ['partition', 'tenant']
    readonly_fields = ('invoice_number', 'created_at', 'created_by', 'updated_at', 'updated_by')
    inlines = [InvoiceItemInline]

    @admin.display(description='Status')
    def computed_status(self, obj):
        return selectors.invoice_status(obj)

    @admin.display(description='Outstanding')
    def computed_outstanding(self, obj):
        return selectors.invoice_outstanding(obj)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'amount', 'method', 'collected_by', 'collected_at', 'is_cancelled')
    list_filter = ('method', 'is_cancelled')
    search_fields = ('invoice__invoice_number', 'reference')
    readonly_fields = [f.name for f in Payment._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
