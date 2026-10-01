from django.core.management.base import BaseCommand
from apps.accounts.models import User
from apps.notifications import services

class Command(BaseCommand):
    help = 'Recompute date/state-based notifications (expense due dates, overdue invoices, contracts, vacancies) for every active user. Schedule daily.'

    def handle(self, *args, **options):
        users = User.objects.filter(is_active=True).exclude(username='system')
        for user in users:
            services.sync_for_user(user)
        self.stdout.write(self.style.SUCCESS(f'Notifications synced for {users.count()} user(s).'))
