from datetime import date
from decimal import Decimal
from django import forms
from django.db.models import Q
from .models import Charge, ChargeType, Payment

class ChargeForm(forms.Form):
    charge_type = forms.ModelChoiceField(queryset=ChargeType.objects.filter(is_active=True), empty_label='Select a charge type…', required=False)
    new_charge_type = forms.CharField(required=False, max_length=100, label='Or a new charge type', help_text='Not in the list? Type its name, e.g. Electricity, Water, Wi-Fi.')
    description = forms.CharField(required=False, max_length=255)
    amount = forms.DecimalField(min_value=Decimal('0'), decimal_places=2, max_digits=12)
    frequency = forms.ChoiceField(choices=[('monthly', 'Monthly'), ('one_time', 'One-time')], initial='monthly')
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, partition=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Rent is set on the tenant (onboarding / edit tenant) so its month-by-month history stays correct.
        types = ChargeType.objects.filter(is_active=True).exclude(name__iexact='Rent')
        if partition:
            today = date.today()
            active_ids = Charge.objects.filter(partition=partition, is_active=True).filter(Q(end_date__isnull=True) | Q(end_date__gt=today)).values_list('charge_type_id', flat=True)
            types = types.exclude(id__in=active_ids)
        self.fields['charge_type'].queryset = types
        self.partition = partition

    def clean(self):
        cleaned = super().clean()
        name = ' '.join((cleaned.pop('new_charge_type', '') or '').split())
        if name and cleaned.get('charge_type') is None:
            if name.lower() == 'rent':
                self.add_error('new_charge_type', 'Rent is set on the tenant, not as a charge.')
            else:
                existing = ChargeType.objects.filter(name__iexact=name).first()
                cleaned['charge_type'] = existing or ChargeType(name=name)
        elif cleaned.get('charge_type') is None:
            self.add_error('charge_type', 'Choose a charge type or type a new one.')
        charge_type = cleaned.get('charge_type')
        if charge_type is not None and charge_type.pk and self.partition is not None:
            today = date.today()
            if Charge.objects.filter(partition=self.partition, charge_type=charge_type, is_active=True).filter(Q(end_date__isnull=True) | Q(end_date__gt=today)).exists():
                self.add_error('charge_type' if 'charge_type' in self.data else 'new_charge_type', f'{charge_type.name} is already an active charge on this partition.')
        return cleaned

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
