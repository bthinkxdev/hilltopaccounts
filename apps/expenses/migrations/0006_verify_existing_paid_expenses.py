from django.db import migrations
from django.db.models import F


def verify_existing_paid(apps, schema_editor):
    # Before the verification step existed, every paid expense was final. Treat them as already verified
    # (by whoever recorded them, on the day they were paid) so only new staff payments await the owner.
    Expense = apps.get_model('expenses', 'Expense')
    Expense.objects.filter(paid_on__isnull=False, verified_at__isnull=True).update(verified_at=F('updated_at'), verified_by=F('created_by'))


class Migration(migrations.Migration):
    dependencies = [('expenses', '0005_expense_verification')]
    operations = [migrations.RunPython(verify_existing_paid, migrations.RunPython.noop)]
