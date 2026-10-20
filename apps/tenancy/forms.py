import re
from datetime import date
from decimal import Decimal
from django import forms

from .models import Tenant

class TenantForm(forms.Form):
    name = forms.CharField(max_length=200)
    mobile = forms.CharField(required=False, max_length=32)
    id_document_number = forms.CharField(required=False, max_length=64, label='ID / Passport number')
    nationality = forms.CharField(required=False, max_length=100)
    move_in_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    monthly_rent = forms.DecimalField(min_value=Decimal('0.01'), decimal_places=2, max_digits=12)
    deposit = forms.DecimalField(required=False, min_value=Decimal('0'), decimal_places=2, max_digits=12, initial=Decimal('0'), label='Total Deposit (QAR)')
    deposit_status = forms.ChoiceField(required=False, choices=Tenant.DepositStatus.choices, initial=Tenant.DepositStatus.PENDING, label='Deposit Status')
    deposit_paid = forms.DecimalField(required=False, min_value=Decimal('0'), decimal_places=2, max_digits=12, initial=Decimal('0'), label='Amount Received (QAR)', help_text='Required if deposit is partially paid.')
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, restrict_past_move_in=False, lock_rent=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.restrict_past_move_in = restrict_past_move_in
        if restrict_past_move_in:
            self.fields['move_in_date'].widget.attrs['min'] = date.today().isoformat()
        if lock_rent:
            self.fields['monthly_rent'].disabled = True
            self.fields['monthly_rent'].help_text = 'Set by the partition rent.'
            if self.is_bound and not self.initial.get('monthly_rent') and 'monthly_rent' in self.data:
                self.initial['monthly_rent'] = self.data.get('monthly_rent')

    def clean_mobile(self):
        mobile = self.cleaned_data.get('mobile', '').strip()
        if mobile and not re.fullmatch(r'\+?[0-9][0-9 \-]{6,17}[0-9]', mobile):
            raise forms.ValidationError('Enter a valid phone number (digits only, optional leading +, 8–18 digits).')
        return mobile

    def clean_nationality(self):
        nationality = self.cleaned_data.get('nationality', '').strip()
        if nationality and not re.fullmatch(r"[^\W\d_]+(?:[ '\-][^\W\d_]+)*", nationality):
            raise forms.ValidationError('Nationality may contain letters only.')
        return nationality

    def clean_move_in_date(self):
        move_in = self.cleaned_data['move_in_date']
        if self.restrict_past_move_in and move_in < date.today():
            raise forms.ValidationError('Move-in date cannot be in the past.')
        return move_in

    def clean(self):
        cleaned_data = super().clean()
        deposit = cleaned_data.get('deposit') or Decimal('0.00')
        status = cleaned_data.get('deposit_status') or Tenant.DepositStatus.PENDING
        paid = cleaned_data.get('deposit_paid') or Decimal('0.00')

        if status == Tenant.DepositStatus.PARTIALLY_PAID and paid <= 0:
            self.add_error('deposit_paid', 'Enter received amount for partially paid deposit.')
        elif status == Tenant.DepositStatus.RECEIVED and paid <= 0:
            paid = deposit

        cleaned_data['deposit'] = deposit
        cleaned_data['deposit_paid'] = paid
        return cleaned_data

class DepositEditForm(forms.Form):
    deposit = forms.DecimalField(min_value=Decimal('0'), decimal_places=2, max_digits=12)
    deposit_paid = forms.DecimalField(required=False, min_value=Decimal('0'), decimal_places=2, max_digits=12, initial=Decimal('0'))

    def clean(self):
        cleaned_data = super().clean()
        deposit = cleaned_data.get('deposit') or Decimal('0.00')
        deposit_paid = cleaned_data.get('deposit_paid') or Decimal('0.00')
        if deposit_paid > deposit:
            self.add_error('deposit_paid', 'Amount received cannot exceed total deposit.')
        cleaned_data['deposit_paid'] = deposit_paid
        return cleaned_data

class MoveOutForm(forms.Form):
    move_out_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False)
