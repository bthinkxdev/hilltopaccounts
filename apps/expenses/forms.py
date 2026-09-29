from decimal import Decimal
from django import forms
from apps.villas.models import Partition, Villa
from .models import Expense, ExpenseCategory

class ExpenseForm(forms.Form):
    villa = forms.ModelChoiceField(queryset=Villa.objects.none(), required=False, empty_label='Whole business (no specific villa)')
    partition = forms.ModelChoiceField(queryset=Partition.objects.none(), required=False, empty_label='Whole villa (no specific partition)')
    category = forms.ModelChoiceField(queryset=ExpenseCategory.objects.filter(is_active=True), empty_label='Select a category…')
    amount = forms.DecimalField(min_value=Decimal('0.01'), decimal_places=2, max_digits=12)
    date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    payment_method = forms.ChoiceField(choices=Expense.Method.choices)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
    reference = forms.CharField(required=False, max_length=100)
    attachment = forms.FileField(required=False)

    def __init__(self, *args, business=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if business is not None and user is not None:
            from apps.accounts import selectors
            self.fields['villa'].queryset = selectors.villas_visible_to(user).filter(business=business)
            self.fields['partition'].queryset = selectors.partitions_visible_to(user).filter(villa__business=business)

    def clean(self):
        cleaned = super().clean()
        villa = cleaned.get('villa')
        partition = cleaned.get('partition')
        if partition and (not villa):
            self.add_error('villa', 'Select the villa this partition belongs to.')
        elif partition and villa and (partition.villa_id != villa.id):
            self.add_error('partition', "This partition doesn't belong to the selected villa.")
        return cleaned
