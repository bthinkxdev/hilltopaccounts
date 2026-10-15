from datetime import date
from django import forms
from apps.shared.forms import ReasonForm
from .models import Business, Partner, PartnerPayout, PartnerVillaShare

ArchiveReasonForm = ReasonForm

class BusinessForm(forms.Form):
    name = forms.CharField(max_length=200, widget=forms.TextInput(attrs={'placeholder': 'e.g. Villa Rentals'}))
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if Business.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError('A business with this name already exists.')
        return name


class PartnerForm(forms.ModelForm):
    class Meta:
        model = Partner
        fields = ['name', 'phone', 'email', 'is_active']


class PartnerShareForm(forms.ModelForm):
    class Meta:
        model = PartnerVillaShare
        fields = ['villa', 'percentage']
        widgets = {
            'percentage': forms.NumberInput(attrs={
                'min': '0.01',
                'max': '100',
                'step': '0.01',
                'placeholder': 'e.g. 20.00',
            }),
        }
        help_texts = {
            'percentage': 'Partner share percentage (e.g. 20.00%). The remaining share automatically belongs to you (e.g. 80.00%).'
        }

    def clean_percentage(self):
        percentage = self.cleaned_data.get('percentage')
        if percentage is not None:
            if percentage <= 0:
                raise forms.ValidationError('Percentage share must be greater than 0%.')
            if percentage > 100:
                raise forms.ValidationError('Percentage share cannot exceed 100%.')
        return percentage

    def __init__(self, *args, partner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.partner = partner
        from apps.villas.models import Villa
        qs = Villa.objects.filter(is_archived=False).order_by('name')
        if partner:
            qs = qs.exclude(partner_shares__partner=partner)
        self.fields['villa'].queryset = qs


class PartnerPayoutForm(forms.ModelForm):
    class Meta:
        model = PartnerPayout
        fields = ['amount', 'payment_method', 'paid_on', 'notes']
        widgets = {
            'paid_on': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount is not None and amount <= 0:
            raise forms.ValidationError('Payout amount must be greater than zero.')
        return amount

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.initial.get('paid_on'):
            self.initial['paid_on'] = date.today()
