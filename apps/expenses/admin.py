from django.contrib import admin
from .models import Expense, ExpenseCategory
from .services import create_expense, update_expense

@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active')
    search_fields = ('name',)

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('category', 'business', 'villa', 'amount', 'date', 'status')
    list_filter = ('business', 'villa', 'category', 'status')
    search_fields = ('description', 'reference')
    autocomplete_fields = ['business', 'villa', 'partition', 'category']
    readonly_fields = ('created_at', 'created_by', 'updated_at', 'updated_by')

    def save_model(self, request, obj, form, change):
        field_values = dict(villa=obj.villa, due_date=obj.due_date, paid_on=obj.paid_on, partition=obj.partition, category=obj.category, amount=obj.amount, date=obj.date, payment_method=obj.payment_method, description=obj.description, reference=obj.reference, attachment=obj.attachment)
        if change:
            update_expense(expense=obj, updated_by=request.user, **field_values)
        else:
            created = create_expense(created_by=request.user, **field_values)
            obj.pk = created.pk

    def has_delete_permission(self, request, obj=None):
        return False
