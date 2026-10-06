from django import forms
from django.contrib.auth.password_validation import validate_password
from apps.businesses.models import Business
from apps.villas.models import Villa
from .models import Assignment, User

class StaffCreateForm(forms.Form):
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False)
    password1 = forms.CharField(widget=forms.PasswordInput, label='Password')
    password2 = forms.CharField(widget=forms.PasswordInput, label='Confirm password')

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
        return cleaned

class AssignmentForm(forms.Form):
    role = forms.ChoiceField(choices=Assignment.Role.choices)
    business = forms.ModelChoiceField(queryset=Business.objects.filter(is_archived=False), required=False, empty_label='Select a business…')
    villa = forms.ModelChoiceField(queryset=Villa.objects.filter(is_archived=False), required=False, empty_label='Select a villa…')

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get('role')
        business = cleaned.get('business')
        villa = cleaned.get('villa')
        if role == Assignment.Role.BUSINESS_MANAGER:
            if not business:
                self.add_error('business', 'Select a business for a Business Manager assignment.')
            cleaned['villa'] = None
        elif role == Assignment.Role.VILLA_STAFF:
            if not villa:
                self.add_error('villa', 'Select a villa for a Villa Staff assignment.')
            cleaned['business'] = None
        elif role == Assignment.Role.ACCOUNTANT:
            if not (business or villa):
                self.add_error(None, 'Select a business or a villa for an Accountant assignment.')
            elif business and villa:
                self.add_error(None, 'Select either a business or a villa for an Accountant assignment, not both.')
        return cleaned
