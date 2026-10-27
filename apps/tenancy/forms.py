import re
from datetime import date
from decimal import Decimal
from django import forms

from .models import DepositCollection

class TenantForm(forms.Form):
    name = forms.CharField(max_length=200)
    mobile = forms.CharField(required=False, max_length=32)
    id_document_number = forms.CharField(required=False, max_length=64, label='ID / Passport number')
    nationality = forms.CharField(required=False, max_length=100)
    move_in_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    monthly_rent = forms.DecimalField(min_value=Decimal('0.01'), decimal_places=2, max_digits=12, label='Monthly Rent (QAR)', help_text='Fixed every month. Collections can be partial; the rest stays pending.')
    deposit = forms.DecimalField(required=False, min_value=Decimal('0'), decimal_places=2, max_digits=12, initial=Decimal('0'), label='Advance / Security Deposit (QAR)', help_text='Leave 0 if there is no advance.')
    deposit_paid = forms.DecimalField(required=False, min_value=Decimal('0'), decimal_places=2, max_digits=12, initial=Decimal('0'), label='Advance Received Now (QAR)', help_text='Can be part of the advance; the rest stays pending.')
    deposit_method = forms.ChoiceField(required=False, choices=DepositCollection.Method.choices, initial=DepositCollection.Method.CASH, label='Advance Payment Method')
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, restrict_past_move_in=False, lock_rent=False, lock_deposit=False, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.restrict_past_move_in = restrict_past_move_in
        self.lock_deposit = lock_deposit
        self.editing = editing
        if editing:
            # The advance already received is changed only by collecting it (dated records), never typed over.
            del self.fields['deposit_paid']
            del self.fields['deposit_method']
            self.fields['monthly_rent'].help_text = 'A new rent applies from the first month not yet billed; earlier months keep their rent.'
        if restrict_past_move_in:
            self.fields['move_in_date'].widget.attrs['min'] = date.today().isoformat()
        if lock_rent:
            self.fields['monthly_rent'].disabled = True
            self.fields['monthly_rent'].help_text = 'Set by the partition rent.'
            if self.is_bound and not self.initial.get('monthly_rent') and 'monthly_rent' in self.data:
                self.initial['monthly_rent'] = self.data.get('monthly_rent')
        if not editing and 'deposit' in self.fields:
            dep_id = self['deposit'].id_for_label
            self.fields['monthly_rent'].widget.attrs['oninput'] = (
                f"var d = document.getElementById('{dep_id}'); if (d && (!d.dataset.custom || d.readOnly)) d.value = this.value;"
            )
            self.fields['deposit'].widget.attrs['oninput'] = (
                "this.dataset.custom = (this.value !== '' && this.value !== '0') ? '1' : '';"
            )
        if lock_deposit and 'deposit' in self.fields:
            self.fields['deposit'].widget.attrs['readonly'] = True
            self.fields['deposit'].widget.attrs['style'] = 'opacity: 0.75; cursor: not-allowed; pointer-events: none;'
            self.fields['deposit'].help_text = 'Fixed to rent amount (staff cannot change).'

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
        if self.lock_deposit and cleaned_data.get('monthly_rent'):
            cleaned_data['deposit'] = cleaned_data['monthly_rent']
        else:
            cleaned_data['deposit'] = cleaned_data.get('deposit') or Decimal('0.00')
        if not self.editing:
            paid = cleaned_data.get('deposit_paid') or Decimal('0.00')
            if paid > cleaned_data['deposit']:
                self.add_error('deposit_paid', 'Advance received cannot be more than the advance.')
            cleaned_data['deposit_paid'] = paid
            cleaned_data['deposit_method'] = cleaned_data.get('deposit_method') or DepositCollection.Method.CASH
        return cleaned_data

class DepositCollectForm(forms.Form):
    """Change the advance amount and/or collect part of what is pending."""
    deposit = forms.DecimalField(min_value=Decimal('0'), decimal_places=2, max_digits=12, label='Advance / Security Deposit (QAR)')
    amount = forms.DecimalField(required=False, min_value=Decimal('0'), decimal_places=2, max_digits=12, initial=Decimal('0'), label='Amount Received Now (QAR)')
    method = forms.ChoiceField(choices=DepositCollection.Method.choices, initial=DepositCollection.Method.CASH, label='Payment Method')
    collected_at = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}), label='Collection Date')

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.fields['collected_at'].widget.attrs['max'] = date.today().isoformat()

    def clean(self):
        cleaned_data = super().clean()
        deposit = cleaned_data.get('deposit')
        amount = cleaned_data.get('amount') or Decimal('0.00')
        cleaned_data['amount'] = amount
        if deposit is None or self.tenant is None:
            return cleaned_data
        if deposit < self.tenant.deposit_paid:
            self.add_error('deposit', f'Cannot be less than what was already collected (QAR {self.tenant.deposit_paid}).')
        elif amount > deposit - self.tenant.deposit_paid:
            self.add_error('amount', f'Only QAR {deposit - self.tenant.deposit_paid} of the advance is pending.')
        collected_at = cleaned_data.get('collected_at')
        if collected_at and collected_at > date.today():
            self.add_error('collected_at', 'Collection date cannot be in the future.')
        return cleaned_data

class MoveOutForm(forms.Form):
    move_out_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False)
