import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

class Migration(migrations.Migration):
    initial = True
    dependencies = [('billing', '0001_initial'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name='CashHandover', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('declared_amount', models.DecimalField(decimal_places=2, max_digits=12)), ('confirmed_amount', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)), ('status', models.CharField(choices=[('submitted', 'Submitted'), ('confirmed', 'Confirmed'), ('rejected', 'Rejected')], default='submitted', max_length=20)), ('submitted_at', models.DateTimeField(auto_now_add=True)), ('confirmed_at', models.DateTimeField(blank=True, null=True)), ('notes', models.TextField(blank=True)), ('rejection_reason', models.TextField(blank=True)), ('confirmed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL)), ('payments', models.ManyToManyField(blank=True, related_name='handovers', to='billing.payment')), ('staff', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cash_handovers', to=settings.AUTH_USER_MODEL))], options={'ordering': ['-submitted_at'], 'constraints': [models.CheckConstraint(condition=models.Q(('declared_amount__gt', 0)), name='handover_declared_amount_positive')]})]
