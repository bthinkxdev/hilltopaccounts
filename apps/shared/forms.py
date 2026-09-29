from django import forms

class ReasonForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Why is this being done?'}), help_text='Recorded in the audit trail.')
