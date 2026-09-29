import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('villas', '0001_initial'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name='Partition', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('name', models.CharField(max_length=100)), ('description', models.TextField(blank=True)), ('status', models.CharField(choices=[('active', 'Active'), ('archived', 'Archived')], default='active', max_length=20)), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL)), ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL)), ('villa', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='partitions', to='villas.villa'))], options={'ordering': ['villa__name', 'name'], 'constraints': [models.UniqueConstraint(fields=('villa', 'name'), name='unique_partition_name_per_villa')]})]
