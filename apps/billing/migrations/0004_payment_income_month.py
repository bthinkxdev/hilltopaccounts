from django.db import migrations, models


def keep_current_figures(apps, schema_editor):
    """Existing payments keep the income month they have today (the month the rent is for), so no past figure changes."""
    Payment = apps.get_model('billing', 'Payment')
    for payment in Payment._base_manager.select_related('invoice').iterator():
        payment.income_month = payment.invoice.billing_period_start.replace(day=1)
        payment.save(update_fields=['income_month'])


class Migration(migrations.Migration):
    dependencies = [('billing', '0003_seed_standard_charge_types')]
    operations = [
        migrations.AddField(model_name='payment', name='income_month', field=models.DateField(null=True, editable=False, db_index=True)),
        migrations.RunPython(keep_current_figures, migrations.RunPython.noop),
        migrations.AlterField(model_name='payment', name='income_month', field=models.DateField(db_index=True, editable=False, help_text='The month this counts as income of: the later of the rent month and the month it was collected. Set once, when recorded.')),
    ]
