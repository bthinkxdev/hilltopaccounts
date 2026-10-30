from django.db import migrations

STANDARD = ['Electricity', 'Water', 'Wifi', 'Cleaning', 'Maintenance', 'Gas', 'Parking']


def seed(apps, schema_editor):
    ChargeType = apps.get_model('billing', 'ChargeType')
    for name in STANDARD:
        ChargeType.objects.get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [('billing', '0002_remove_invoice_unique_invoice_per_partition_per_period')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
