from django.db import migrations


def rename(apps, schema_editor):
    Category = apps.get_model('expenses', 'ExpenseCategory')
    old = Category.objects.filter(name__iexact='Maintenance').first()
    if old and not Category.objects.filter(name__iexact='Company Service').exists():
        old.name = 'Company Service'
        old.save(update_fields=['name'])


class Migration(migrations.Migration):
    dependencies = [('expenses', '0009_expense_auto_debit_skipped')]
    operations = [migrations.RunPython(rename, migrations.RunPython.noop)]
