from decimal import Decimal
from django import forms
from apps.billing.models import Payment

class HandoverSubmitForm(forms.Form):
    payments = forms.ModelMultipleChoiceField(queryset=Payment.objects.none(), widget=forms.CheckboxSelectMultiple)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, staff=None, **kwargs):
        super().__init__(*args, **kwargs)
        if staff is not None:
            from . import selectors
            self.fields['payments'].queryset = selectors.unclaimed_cash_payments(staff)

class ConfirmHandoverForm(forms.Form):
    confirmed_amount = forms.DecimalField(min_value=Decimal('0'), decimal_places=2, max_digits=12)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
