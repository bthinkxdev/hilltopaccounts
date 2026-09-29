from decimal import Decimal
from django import forms

class TenantForm(forms.Form):
    name = forms.CharField(max_length=200)
    mobile = forms.CharField(required=False, max_length=32)
    id_document_number = forms.CharField(required=False, max_length=64, label='ID / Passport number')
    nationality = forms.CharField(required=False, max_length=100)
    move_in_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    monthly_rent = forms.DecimalField(min_value=Decimal('0.01'), decimal_places=2, max_digits=12)
    deposit = forms.DecimalField(required=False, min_value=Decimal('0'), decimal_places=2, max_digits=12, initial=Decimal('0'))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def clean_deposit(self):
        return self.cleaned_data.get('deposit') or Decimal('0.00')

class MoveOutForm(forms.Form):
    move_out_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False)
