from django.conf import settings
from django.db import models

class Villa(models.Model):
    business = models.ForeignKey('businesses.Business', on_delete=models.PROTECT, related_name='villas')
    name = models.CharField(max_length=200)
    address = models.TextField(blank=True)
    landlord_name = models.CharField(max_length=200, blank=True)
    landlord_contact = models.CharField(max_length=200, blank=True)
    contract_start = models.DateField(null=True, blank=True)
    contract_end = models.DateField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')

    class Meta:
        ordering = ['business__name', 'name']
        constraints = [models.UniqueConstraint(fields=['business', 'name'], name='unique_villa_name_per_business')]

    def __str__(self):
        return f'{self.name} ({self.business.name})'

class Partition(models.Model):

    class Status(models.TextChoices):
        ACTIVE = ('active', 'Active')
        ARCHIVED = ('archived', 'Archived')
    villa = models.ForeignKey(Villa, on_delete=models.PROTECT, related_name='partitions')
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True)

    class Meta:
        ordering = ['villa__name', 'name']
        constraints = [models.UniqueConstraint(fields=['villa', 'name'], name='unique_partition_name_per_villa')]

    @property
    def business(self):
        return self.villa.business

    @property
    def current_tenant(self):
        from apps.tenancy.models import Tenant
        return self.tenancies.filter(status=Tenant.Status.ACTIVE).first()

    @property
    def is_occupied(self) -> bool:
        return self.current_tenant is not None

    def __str__(self):
        return f'{self.name} — {self.villa.name}'
