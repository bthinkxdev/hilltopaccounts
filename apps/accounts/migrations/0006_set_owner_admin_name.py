from django.db import migrations


def set_owner_admin_name(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(is_owner=True).update(first_name='Admin', last_name='')
    User.objects.filter(username='owner').update(first_name='Admin', last_name='')


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_user_whatsapp_number'),
    ]

    operations = [
        migrations.RunPython(set_owner_admin_name, migrations.RunPython.noop),
    ]
