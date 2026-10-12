from django import forms

class ReasonForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Why is this being done?'}), help_text='Recorded in the audit trail.')

class TaggedSelect(forms.Select):

    def __init__(self, *args, data_attr, instance_attr, **kwargs):
        super().__init__(*args, **kwargs)
        self.data_attr = data_attr
        self.instance_attr = instance_attr

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        instance = getattr(value, 'instance', None)
        if instance is not None:
            option['attrs'][self.data_attr] = getattr(instance, self.instance_attr)
        return option

