from django.db import migrations, models


def keep_current_figures(apps, schema_editor):
    """Existing advances keep the income month they have today (the move-in month), so no past figure changes."""
    DepositCollection = apps.get_model('tenancy', 'DepositCollection')
    for deposit in DepositCollection._base_manager.select_related('tenant').iterator():
        deposit.income_month = deposit.tenant.move_in_date.replace(day=1)
        deposit.save(update_fields=['income_month'])


class Migration(migrations.Migration):
    dependencies = [('tenancy', '0005_depositcollection_void_reason_and_more')]
    operations = [
        migrations.AddField(model_name='depositcollection', name='income_month', field=models.DateField(null=True, editable=False, db_index=True)),
        migrations.RunPython(keep_current_figures, migrations.RunPython.noop),
        migrations.AlterField(model_name='depositcollection', name='income_month', field=models.DateField(db_index=True, editable=False, help_text='The month this advance counts as income of: the later of the move-in month and the month it was collected. Set once, when recorded.')),
    ]
