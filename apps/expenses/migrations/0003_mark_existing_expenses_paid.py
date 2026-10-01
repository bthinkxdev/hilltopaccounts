from django.db import migrations
from django.db.models import F


def mark_existing_paid(apps, schema_editor):
    # Before due dates existed every expense was recorded with a payment method, i.e. as already paid.
    Expense = apps.get_model('expenses', 'Expense')
    Expense.objects.filter(paid_on__isnull=True).exclude(payment_method='').update(paid_on=F('date'))


class Migration(migrations.Migration):
    dependencies = [('expenses', '0002_due_dates_and_paid_status')]
    operations = [migrations.RunPython(mark_existing_paid, migrations.RunPython.noop)]
