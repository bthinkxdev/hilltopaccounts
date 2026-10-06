from django import forms
from apps.shared.forms import ReasonForm
ArchiveReasonForm = ReasonForm

class BusinessForm(forms.Form):
    name = forms.CharField(max_length=200, widget=forms.TextInput(attrs={'placeholder': 'e.g. Villa Rentals'}))
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def clean_name(self):
        from .models import Business
        name = self.cleaned_data['name'].strip()
        if Business.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError('A business with this name already exists.')
        return name
