import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name='Business', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('name', models.CharField(max_length=200, unique=True)), ('description', models.TextField(blank=True)), ('is_archived', models.BooleanField(default=False)), ('created_at', models.DateTimeField(auto_now_add=True)), ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL))], options={'verbose_name_plural': 'businesses', 'ordering': ['name']})]
