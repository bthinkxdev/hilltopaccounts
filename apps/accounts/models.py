from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    is_owner = models.BooleanField(default=False, help_text='Sees and can operate on every business/villa/financial record.')
    phone = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ['username']

    def __str__(self):
        return self.get_full_name() or self.username

class Assignment(models.Model):

    class Role(models.TextChoices):
        BUSINESS_MANAGER = ('business_manager', 'Business Manager')
        VILLA_STAFF = ('villa_staff', 'Villa Staff')
        ACCOUNTANT = ('accountant', 'Accountant')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='assignments')
    role = models.CharField(max_length=20, choices=Role.choices)
    business = models.ForeignKey('businesses.Business', on_delete=models.CASCADE, null=True, blank=True, related_name='manager_assignments')
    villa = models.ForeignKey('villas.Villa', on_delete=models.CASCADE, null=True, blank=True, related_name='staff_assignments')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')

    class Meta:
        ordering = ['user__username']
        constraints = [models.CheckConstraint(check=models.Q(role='business_manager', business__isnull=False, villa__isnull=True) | models.Q(role='villa_staff', villa__isnull=False, business__isnull=True) | models.Q(role='accountant', business__isnull=False, villa__isnull=True) | models.Q(role='accountant', business__isnull=True, villa__isnull=False), name='assignment_scope_matches_role'), models.UniqueConstraint(fields=['user', 'role', 'business', 'villa'], name='unique_assignment')]

    def __str__(self):
        scope = self.business or self.villa
        return f'{self.user} - {self.get_role_display()} ({scope})'
