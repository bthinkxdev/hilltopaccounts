import re
from django import forms
from django.contrib.auth.password_validation import validate_password
from apps.businesses.models import Business, Partner
from apps.shared.forms import TaggedSelect
from apps.villas.models import Villa
from .models import Assignment, User

def validate_whatsapp(value):
    value = (value or '').strip()
    if not re.fullmatch(r'\+?[0-9][0-9 \-]{6,17}[0-9]', value):
        raise forms.ValidationError('Enter a valid WhatsApp number (digits only, optional leading +, 8–18 digits).')
    return value

class StaffCreateForm(forms.Form):
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False)
    whatsapp_number = forms.CharField(max_length=32, label='WhatsApp number', help_text='Include the country code, e.g. +974 5555 1234. Used for listing enquiries.')
    password1 = forms.CharField(widget=forms.PasswordInput, label='Password')
    password2 = forms.CharField(widget=forms.PasswordInput, label='Confirm password')
    is_partner = forms.BooleanField(required=False, label='Partner Account', widget=forms.CheckboxInput(attrs={'id': 'id_is_partner', 'class': 'switch__input'}))
    partner = forms.ModelChoiceField(queryset=Partner.objects.filter(is_active=True), required=False, empty_label='Select Partner…', widget=forms.Select(attrs={'id': 'id_partner'}))
    villa = forms.ModelChoiceField(queryset=Villa.objects.filter(is_archived=False, is_sold=False), required=False, empty_label='Select Villa…', widget=forms.Select(attrs={'id': 'id_villa'}))

    def clean_whatsapp_number(self):
        return validate_whatsapp(self.cleaned_data['whatsapp_number'])

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('A user with this username already exists.')
        return username

    def clean(self):
        cleaned = super().clean()
        p1, p2 = (cleaned.get('password1'), cleaned.get('password2'))
        if p1 and p2:
            if p1 != p2:
                self.add_error('password2', 'Passwords do not match.')
            else:
                try:
                    validate_password(p1)
                except forms.ValidationError as exc:
                    self.add_error('password1', exc)
        if cleaned.get('is_partner'):
            partner = cleaned.get('partner')
            if not partner:
                self.add_error('partner', 'Please select a partner to link.')
            elif partner.user_id:
                username_hint = f' (@{partner.user.username})' if partner.user else ''
                self.add_error('partner', f'{partner.name} already has an active account{username_hint}. A partner cannot be linked to more than one user account.')
            if not cleaned.get('villa'):
                self.add_error('villa', 'Please select a villa for this partner.')
        return cleaned

class StaffPasswordChangeForm(forms.Form):
    new_password1 = forms.CharField(widget=forms.PasswordInput, label='New password')
    new_password2 = forms.CharField(widget=forms.PasswordInput, label='New password confirmation')

    def __init__(self, staff, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.staff = staff

    def clean(self):
        cleaned = super().clean()
        p1, p2 = (cleaned.get('new_password1'), cleaned.get('new_password2'))
        if p1 and p2:
            if p1 != p2:
                self.add_error('new_password2', 'Passwords do not match.')
            else:
                try:
                    validate_password(p1)
                except forms.ValidationError as exc:
                    self.add_error('new_password1', exc)
        return cleaned

    def save(self, commit=True):
        self.staff.set_password(self.cleaned_data['new_password1'])
        if commit:
            self.staff.save()
        return self.staff

class StaffWhatsAppForm(forms.Form):
    whatsapp_number = forms.CharField(max_length=32, label='WhatsApp number', help_text='Include the country code, e.g. +974 5555 1234.')

    def clean_whatsapp_number(self):
        return validate_whatsapp(self.cleaned_data['whatsapp_number'])

class AssignmentForm(forms.Form):
    role = forms.ChoiceField(
        choices=[(r.value, r.label) for r in Assignment.Role if r != Assignment.Role.PARTNER]
    )
    business = forms.ModelChoiceField(queryset=Business.objects.filter(is_archived=False), required=False, empty_label='Select a business…')
    villa = forms.ModelChoiceField(
        queryset=Villa.objects.filter(is_archived=False, is_sold=False, business__is_archived=False).select_related('business'),
        required=False,
        empty_label='Select a villa…',
        widget=TaggedSelect(data_attr='data-business', instance_attr='business_id'),
    )

    def __init__(self, *args, staff=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.staff = staff

    def clean(self):
        cleaned = super().clean()
        role, business, villa = cleaned.get('role'), cleaned.get('business'), cleaned.get('villa')
        if villa and villa.is_sold:
            self.add_error('villa', 'Cannot assign staff to a sold villa.')
        if self.staff:
            villa_staff_biz = set(Assignment.objects.filter(user=self.staff, role=Assignment.Role.VILLA_STAFF).values_list('villa__business_id', flat=True))
            manager_biz = set(Assignment.objects.filter(user=self.staff, role=Assignment.Role.BUSINESS_MANAGER).values_list('business_id', flat=True))
            if role == Assignment.Role.BUSINESS_MANAGER:
                if business and business.id in villa_staff_biz:
                    self.add_error('business', f'{self.staff.username} is already assigned as Villa Staff to a villa in {business.name}.')
            elif role == Assignment.Role.VILLA_STAFF:
                if villa and villa.business_id in manager_biz:
                    self.add_error('villa', f'{self.staff.username} is already the Business Manager of {villa.business.name} and cannot be assigned to its villas.')

        if role == Assignment.Role.BUSINESS_MANAGER:
            if not business:
                self.add_error('business', 'Select a business for a Business Manager assignment.')
            cleaned['villa'] = None
        else:
            if villa and business and villa.business_id != business.id:
                self.add_error('villa', 'The selected villa does not belong to the selected business.')
            if role == Assignment.Role.VILLA_STAFF:
                if not villa:
                    self.add_error('villa', 'Select a villa for a Villa Staff assignment.')
                cleaned['business'] = None
            elif role == Assignment.Role.ACCOUNTANT:
                if not (business or villa):
                    self.add_error(None, 'Select a business or a villa for an Accountant assignment.')
                elif business and villa:
                    cleaned['business'] = None
        return cleaned
