import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    initial = True
    dependencies = [('businesses', '0001_initial'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name='Villa', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('name', models.CharField(max_length=200)), ('address', models.TextField(blank=True)), ('landlord_name', models.CharField(blank=True, max_length=200)), ('landlord_contact', models.CharField(blank=True, max_length=200)), ('contract_start', models.DateField(blank=True, null=True)), ('contract_end', models.DateField(blank=True, null=True)), ('is_archived', models.BooleanField(default=False)), ('created_at', models.DateTimeField(auto_now_add=True)), ('business', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='villas', to='businesses.business')), ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL))], options={'ordering': ['business__name', 'name'], 'constraints': [models.UniqueConstraint(fields=('business', 'name'), name='unique_villa_name_per_business')]})]
