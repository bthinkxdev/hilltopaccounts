from django.core.management.base import BaseCommand
from apps.accounts.services import get_system_user
from apps.expenses.models import Expense
from apps.expenses.services import cancel_expense


class Command(BaseCommand):
    help = ('Cancel fixed expenses that were generated for months before the fixed expense was set up. '
            'Only unpaid ones are cancelled (with a reason); paid ones are listed for manual review. Dry run unless --apply.')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually cancel them (default: only list).')

    def handle(self, *args, apply=False, **options):
        system_user = get_system_user() if apply else None
        cancelled = review = 0
        for expense in Expense.objects.filter(recurring__isnull=False, status=Expense.Status.ACTIVE).select_related('recurring', 'villa', 'category').order_by('villa__name', 'period'):
            if expense.period >= expense.recurring.first_period:
                continue
            label = f'{expense.villa.name} · {expense.category.name} · {expense.period:%B %Y} · QAR {expense.amount}'
            if expense.paid_on is not None:
                review += 1
                self.stdout.write(self.style.WARNING(f'PAID — review manually: {label}'))
                continue
            cancelled += 1
            self.stdout.write(f'{"Cancelled" if apply else "Would cancel"}: {label}')
            if apply:
                cancel_expense(expense=expense, cancelled_by=system_user, reason=f'Generated for a month before this fixed expense was set up ({expense.recurring.first_period:%B %Y}).')
        self.stdout.write(self.style.SUCCESS(f'{cancelled} unpaid back-filled expense(s) {"cancelled" if apply else "found"}; {review} paid one(s) need manual review.'))
