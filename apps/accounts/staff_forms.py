from django import forms
from django.contrib.auth.password_validation import validate_password
from apps.businesses.models import Business
from apps.shared.forms import TaggedSelect
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
    villa = forms.ModelChoiceField(
        queryset=Villa.objects.filter(is_archived=False).select_related('business'),
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
