from django import forms
from apps.shared.forms import ReasonForm
ArchiveReasonForm = ReasonForm

class BusinessForm(forms.Form):
    name = forms.CharField(max_length=200, widget=forms.TextInput(attrs={'placeholder': 'e.g. Villa Rentals'}))
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))
