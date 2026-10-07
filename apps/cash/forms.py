from decimal import Decimal
from django import forms
from apps.billing.models import Payment
from apps.expenses.models import Expense

class HandoverSubmitForm(forms.Form):
    payments = forms.ModelMultipleChoiceField(queryset=Payment.objects.none(), widget=forms.CheckboxSelectMultiple)
    expenses = forms.ModelMultipleChoiceField(queryset=Expense.objects.none(), required=False, widget=forms.CheckboxSelectMultiple, label='Expenses you paid from this cash', help_text='You hand over: selected collections minus selected expenses.')
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, staff=None, **kwargs):
        super().__init__(*args, **kwargs)
        if staff is not None:
            from . import selectors
            payments_qs = selectors.unclaimed_cash_payments(staff).select_related('invoice', 'invoice__partition__villa')
            expenses_qs = selectors.unclaimed_staff_expenses(staff).select_related('category', 'villa')
            self.fields['payments'].queryset = payments_qs
            self.fields['expenses'].queryset = expenses_qs
            self.fields['payments'].label_from_instance = lambda p: f'QAR {p.amount} — {p.invoice.partition.villa.name} / {p.invoice.partition.name} — {p.collected_at}'
            self.fields['expenses'].label_from_instance = lambda e: f'{e.category} — QAR {e.amount} — {e.villa.name} — {e.paid_on}'
            if not self.is_bound:
                self.fields['payments'].initial = list(payments_qs.values_list('pk', flat=True))
                total_coll = sum((p.amount for p in payments_qs), Decimal('0.00'))
                total_exp = sum((e.amount for e in expenses_qs), Decimal('0.00'))
                if total_coll > total_exp:
                    self.fields['expenses'].initial = list(expenses_qs.values_list('pk', flat=True))

class ConfirmHandoverForm(forms.Form):
    confirmed_amount = forms.DecimalField(min_value=Decimal('0'), decimal_places=2, max_digits=12)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
