from django import forms
from apps.businesses.forms import ArchiveReasonForm
from apps.businesses.models import Business
from .models import Villa

class VillaForm(forms.Form):
    business = forms.ModelChoiceField(queryset=Business.objects.none(), empty_label='Select a business…')
    name = forms.CharField(max_length=200)
    address = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
    landlord_name = forms.CharField(required=False, max_length=200)
    landlord_contact = forms.CharField(required=False, max_length=200)
    contract_start = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    contract_end = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, business_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if business_queryset is not None:
            self.fields['business'].queryset = business_queryset

class PartitionForm(forms.Form):
    villa = forms.ModelChoiceField(queryset=Villa.objects.none(), empty_label='Select a villa…')
    name = forms.CharField(max_length=100, widget=forms.TextInput(attrs={'placeholder': 'e.g. Room 1'}))
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, villa_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if villa_queryset is not None:
            self.fields['villa'].queryset = villa_queryset


class PhotoForm(forms.Form):
    image = forms.FileField(label='Photo', widget=forms.ClearableFileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'}), help_text='JPG, PNG or WebP, up to 5 MB.')
    caption = forms.CharField(required=False, max_length=200)

    def clean_image(self):
        from .photos import validate_photo
        image = self.cleaned_data['image']
        validate_photo(image)
        return image
