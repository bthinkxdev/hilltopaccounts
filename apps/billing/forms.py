from datetime import date
from decimal import Decimal
from django import forms
from .models import ChargeType, Payment

class ChargeForm(forms.Form):
    charge_type = forms.ModelChoiceField(queryset=ChargeType.objects.filter(is_active=True), empty_label='Select a charge type…')
    description = forms.CharField(required=False, max_length=255)
    amount = forms.DecimalField(min_value=Decimal('0'), decimal_places=2, max_digits=12)
    frequency = forms.ChoiceField(choices=[('monthly', 'Monthly'), ('one_time', 'One-time')], initial='monthly')
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))

class NewChargeTypeForm(forms.Form):
    name = forms.CharField(max_length=100)

class InvoiceGenerateForm(forms.Form):
    billing_period_start = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    billing_period_end = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    issue_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    due_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get('billing_period_start')
        end = cleaned.get('billing_period_end')
        if start and end and (end < start):
            self.add_error('billing_period_end', 'Billing period end must be after the start.')
        issue = cleaned.get('issue_date')
        due = cleaned.get('due_date')
        if issue and issue > date.today():
            self.add_error('issue_date', 'Issue date cannot be in the future.')
        if issue and due and due < issue:
            self.add_error('due_date', 'Due date cannot be before the issue date.')
        return cleaned

class PaymentForm(forms.Form):
    amount = forms.DecimalField(min_value=Decimal('0.01'), decimal_places=2, max_digits=12)
    method = forms.ChoiceField(choices=Payment.Method.choices)
    collected_at = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    reference = forms.CharField(required=False, max_length=100)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))

class PaymentCorrectionForm(forms.Form):
    new_amount = forms.DecimalField(min_value=Decimal('0.01'), decimal_places=2, max_digits=12, label='Corrected amount')
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), help_text='Recorded in the audit trail.')
