from decimal import Decimal
from django import forms
from apps.villas.models import Partition, Villa
from .models import Expense, ExpenseCategory

class _TaggedSelect(forms.Select):
    """Select whose options carry a data-<attr> taken from the model instance, for client-side filtering."""

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

class PartitionChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        tenant = obj.current_tenant
        if tenant:
            return f"{obj.name} (Tenant: {tenant.name})"
        return f"{obj.name} (Vacant)"

class ExpenseForm(forms.Form):
    villa = forms.ModelChoiceField(queryset=Villa.objects.none(), empty_label='Select a villa…', widget=forms.Select())
    partition = PartitionChoiceField(queryset=Partition.objects.none(), required=False, empty_label='Whole villa (no specific partition)', widget=_TaggedSelect(data_attr='data-villa', instance_attr='villa_id'))
    name = forms.CharField(max_length=100, label='Expense', widget=forms.TextInput(attrs={'list': 'expense-names', 'autocomplete': 'off', 'placeholder': 'e.g. Electricity'}))
    amount = forms.DecimalField(min_value=Decimal('0.01'), decimal_places=2, max_digits=12, widget=forms.NumberInput(attrs={'inputmode': 'decimal', 'step': '0.01'}))
    date = forms.DateField(label='Expense date', widget=forms.DateInput(attrs={'type': 'date'}))
    mark_paid = forms.BooleanField(required=False, label='Already paid', help_text='Paid by you — staff payments come out of the cash you collected.')
    payment_method = forms.ChoiceField(required=False, choices=[('', 'Select a method…')] + list(Expense.Method.choices), help_text='Needed when the expense is already paid.')
    description = forms.CharField(required=False, label='Notes', widget=forms.Textarea(attrs={'rows': 2}))
    reference = forms.CharField(required=False, max_length=100)
    attachment = forms.FileField(required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user is not None:
            from apps.accounts import selectors
            villas = selectors.manageable_villas(user).filter(is_sold=False)
            if not user.is_owner:
                villas = villas.filter(is_archived=False)
            self.fields['villa'].queryset = villas.select_related('business')
            self.fields['partition'].queryset = Partition.objects.filter(villa__in=villas).select_related('villa') if user.is_owner else Partition.objects.filter(villa__in=villas, status=Partition.Status.ACTIVE).select_related('villa')

    def clean_name(self):
        return ' '.join(self.cleaned_data['name'].split())

    def clean(self):
        cleaned = super().clean()
        villa = cleaned.get('villa')
        partition = cleaned.get('partition')
        if villa and villa.is_sold:
            self.add_error('villa', 'Cannot add expenses to a sold villa.')
        if partition and partition.villa.is_sold:
            self.add_error('partition', 'Cannot add expenses to a sold villa.')
        if partition and villa and partition.villa_id != villa.id:
            self.add_error('partition', "This partition doesn't belong to the selected villa.")
        if partition and cleaned.get('name', '').strip().lower() in ('villa rent', 'villa owner rent'):
            self.add_error('name', "Villa rent is a whole-villa expense, not a partition expense.")
        if cleaned.get('mark_paid') and not cleaned.get('payment_method'):
            self.add_error('payment_method', 'Choose how this was paid.')
        return cleaned

class MarkPaidForm(forms.Form):
    paid_on = forms.DateField(label='Paid on', widget=forms.DateInput(attrs={'type': 'date'}))
    payment_method = forms.ChoiceField(choices=Expense.Method.choices, label='Method')


class RecurringExpenseForm(forms.Form):
    name = forms.CharField(max_length=100, label='Expense', widget=forms.TextInput(attrs={'list': 'expense-names', 'autocomplete': 'off', 'placeholder': 'e.g. Villa owner rent'}))
    amount = forms.DecimalField(min_value=Decimal('0.01'), decimal_places=2, max_digits=12, widget=forms.NumberInput(attrs={'inputmode': 'decimal', 'step': '0.01'}))
    due_day = forms.IntegerField(min_value=1, max_value=28, initial=1, widget=forms.HiddenInput(), required=False)
    auto_debit = forms.BooleanField(required=False, label='Auto-debit from the owner account on the due date')

    def __init__(self, *args, user=None, can_auto_debit=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not (can_auto_debit if can_auto_debit is not None else (user is not None and user.is_owner)):
            self.fields.pop('auto_debit', None)

    def clean_name(self):
        return ' '.join(self.cleaned_data['name'].split())

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('due_day'):
            cleaned['due_day'] = 1
        return cleaned

class ReviseExpenseForm(forms.Form):
    amount = forms.DecimalField(min_value=Decimal('0.01'), decimal_places=2, max_digits=12, widget=forms.NumberInput(attrs={'inputmode': 'decimal', 'step': '0.01'}))
    description = forms.CharField(required=False, label='Notes', widget=forms.Textarea(attrs={'rows': 2}))
