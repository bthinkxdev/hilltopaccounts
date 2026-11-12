import re
from django.db import migrations, models


def backfill(apps, schema_editor):
    """Keep every existing expense where it is today: assigned to the side it was already set for (Admin / Staff)."""
    Recurring = apps.get_model('expenses', 'RecurringExpense')
    Expense = apps.get_model('expenses', 'Expense')
    Recurring.objects.filter(paid_by='owner').update(assigned_to='owner')
    # One-time expenses: a paid one keeps the side that paid it; one still unpaid stays payable by staff (as it is today),
    # except what only the Admin can settle (landlord rent).
    Expense.objects.filter(recurring__isnull=True, paid_by='owner', paid_on__isnull=False).update(assigned_to='owner')
    for expense in Expense.objects.filter(recurring__isnull=True, paid_by='owner', paid_on__isnull=True).select_related('category'):
        if re.search(r'(villa.*rent|landlord|^rent$)', expense.category.name, re.I):
            Expense.objects.filter(pk=expense.pk).update(assigned_to='owner')
    Expense.objects.filter(recurring__isnull=False, recurring__paid_by='owner').update(assigned_to='owner')


class Migration(migrations.Migration):
    dependencies = [('expenses', '0010_rename_maintenance_company_service')]
    operations = [
        migrations.AddField(model_name='recurringexpense', name='assigned_to', field=models.CharField(choices=[('staff', 'Staff'), ('owner', 'Admin')], default='staff', help_text='Who is going to pay it (Admin or Staff). Staff can only view an expense assigned to the Admin.', max_length=10)),
        migrations.AddField(model_name='expense', name='assigned_to', field=models.CharField(choices=[('staff', 'Staff'), ('owner', 'Admin')], default='staff', help_text='Who is going to pay it (Admin or Staff). Staff can only view an expense assigned to the Admin.', max_length=10)),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
