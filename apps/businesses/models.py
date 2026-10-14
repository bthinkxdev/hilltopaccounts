from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

class Business(models.Model):
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'businesses'

    def __str__(self):
        return self.name

class Partner(models.Model):
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class PartnerVillaShare(models.Model):
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name='villa_shares')
    villa = models.ForeignKey('villas.Villa', on_delete=models.CASCADE, related_name='partner_shares')
    percentage = models.DecimalField(max_digits=5, decimal_places=2, help_text='Percentage share, e.g. 40.00%')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['villa__name', 'partner__name']
        constraints = [
            models.UniqueConstraint(fields=['partner', 'villa'], name='unique_partner_villa_share'),
            models.CheckConstraint(check=models.Q(percentage__gt=0, percentage__lte=100), name='partner_share_percentage_range'),
        ]

    def clean(self):
        super().clean()
        if self.percentage is not None and self.villa_id:
            from decimal import Decimal
            qs = PartnerVillaShare.objects.filter(villa=self.villa)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            total = qs.aggregate(t=models.Sum('percentage'))['t'] or Decimal('0.00')
            if total + self.percentage > Decimal('100.00'):
                raise ValidationError({'percentage': f'Total partner shares for {self.villa.name} cannot exceed 100% (currently {total}%, remaining share for you is {Decimal("100.00") - total}%).'})

    def __str__(self):
        return f'{self.partner.name} — {self.villa.name} ({self.percentage}%)'


class PartnerPayout(models.Model):
    class Method(models.TextChoices):
        BANK_TRANSFER = 'bank_transfer', 'Bank Transfer'
        CASH = 'cash', 'Cash'
        OTHER = 'other', 'Other'

    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name='payouts')
    villa = models.ForeignKey('villas.Villa', on_delete=models.SET_NULL, null=True, blank=True, related_name='partner_payouts')
    period_year = models.IntegerField()
    period_month = models.IntegerField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=Method.choices, default=Method.BANK_TRANSFER)
    paid_on = models.DateField()
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')

    class Meta:
        ordering = ['-paid_on', '-created_at']

    def __str__(self):
        return f'{self.partner.name} — QAR {self.amount} ({self.period_year}-{self.period_month:02d})'
