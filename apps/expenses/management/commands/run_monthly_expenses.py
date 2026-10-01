from datetime import date
from django.core.management.base import BaseCommand
from apps.expenses.models import RecurringExpense
from apps.expenses.services import generate_recurring_expenses_as_system, settle_auto_debits
from apps.villas.models import Villa

class Command(BaseCommand):
    help = 'Create the current month\'s fixed expenses for every villa and settle auto-debit expenses that have fallen due. Schedule daily.'

    def handle(self, *args, **options):
        today = date.today()
        villa_ids = RecurringExpense.objects.filter(is_active=True, villa__is_archived=False).values_list('villa_id', flat=True).distinct()
        created = 0
        for villa in Villa.objects.filter(pk__in=villa_ids).select_related('business'):
            created += len(generate_recurring_expenses_as_system(villa=villa, year=today.year, month=today.month))
        settled = settle_auto_debits(today=today)
        self.stdout.write(self.style.SUCCESS(f'{created} fixed expense(s) created, {settled} auto-debit(s) settled.'))
