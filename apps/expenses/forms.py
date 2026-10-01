from decimal import Decimal
from django import forms
from apps.villas.models import Partition, Villa
from .models import Expense, ExpenseCategory, PaidBy

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

class ExpenseForm(forms.Form):
    villa = forms.ModelChoiceField(queryset=Villa.objects.none(), empty_label='Select a villa…', widget=forms.Select())
    partition = forms.ModelChoiceField(queryset=Partition.objects.none(), required=False, empty_label='Whole villa (no specific partition)', widget=_TaggedSelect(data_attr='data-villa', instance_attr='villa_id'))
    name = forms.CharField(max_length=100, label='Expense', widget=forms.TextInput(attrs={'list': 'expense-names', 'autocomplete': 'off', 'placeholder': 'e.g. Electricity'}))
    amount = forms.DecimalField(min_value=Decimal('0.01'), decimal_places=2, max_digits=12, widget=forms.NumberInput(attrs={'inputmode': 'decimal', 'step': '0.01'}))
    date = forms.DateField(label='Expense date', widget=forms.DateInput(attrs={'type': 'date'}))
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}), help_text='When this must be paid. Drives reminders.')
    paid_by = forms.ChoiceField(required=False, choices=PaidBy.choices, label='Paid by', help_text='Staff-paid expenses come out of the cash the staff collected; owner-paid ones are settled from the owner account.')
    mark_paid = forms.BooleanField(required=False, label='Already paid')
    payment_method = forms.ChoiceField(required=False, choices=[('', 'Select a method…')] + list(Expense.Method.choices), help_text='Needed when the expense is already paid.')
    description = forms.CharField(required=False, label='Notes', widget=forms.Textarea(attrs={'rows': 2}))
    reference = forms.CharField(required=False, max_length=100)
    attachment = forms.FileField(required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user is not None:
            from apps.accounts import selectors
            villas = selectors.manageable_villas(user).filter(is_archived=False)
            self.fields['villa'].queryset = villas.select_related('business')
            self.fields['partition'].queryset = Partition.objects.filter(villa__in=villas, status=Partition.Status.ACTIVE).select_related('villa')

    def clean_name(self):
        return ' '.join(self.cleaned_data['name'].split())

    def clean_paid_by(self):
        # Left blank (e.g. a quick mobile entry): the Owner pays from their account, staff from collected cash.
        chosen = self.cleaned_data.get('paid_by')
        if chosen:
            return chosen
        return PaidBy.OWNER if self.user is not None and self.user.is_owner else PaidBy.STAFF

    def clean(self):
        cleaned = super().clean()
        villa = cleaned.get('villa')
        partition = cleaned.get('partition')
        if partition and villa and partition.villa_id != villa.id:
            self.add_error('partition', "This partition doesn't belong to the selected villa.")
        if cleaned.get('mark_paid') and not cleaned.get('payment_method'):
            self.add_error('payment_method', 'Choose how this was paid.')
        due, spent = cleaned.get('due_date'), cleaned.get('date')
        if due and spent and due < spent:
            self.add_error('due_date', 'Due date cannot be before the expense date.')
        return cleaned

class MarkPaidForm(forms.Form):
    paid_on = forms.DateField(label='Paid on', widget=forms.DateInput(attrs={'type': 'date'}))
    payment_method = forms.ChoiceField(choices=Expense.Method.choices, label='Method')


class RecurringExpenseForm(forms.Form):
    name = forms.CharField(max_length=100, label='Expense', widget=forms.TextInput(attrs={'list': 'expense-names', 'autocomplete': 'off', 'placeholder': 'e.g. Villa owner rent'}))
    amount = forms.DecimalField(min_value=Decimal('0.01'), decimal_places=2, max_digits=12, widget=forms.NumberInput(attrs={'inputmode': 'decimal', 'step': '0.01'}))
    due_day = forms.IntegerField(min_value=1, max_value=28, initial=1, label='Due day of month')
    paid_by = forms.ChoiceField(choices=PaidBy.choices, initial=PaidBy.OWNER, label='Paid by')
    auto_debit = forms.BooleanField(required=False, label='Auto-debit from the owner account on the due date')

    def clean_name(self):
        return ' '.join(self.cleaned_data['name'].split())

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('auto_debit') and cleaned.get('paid_by') != PaidBy.OWNER:
            self.add_error('auto_debit', 'Only owner-account expenses can be auto-debited.')
        return cleaned

class ReviseExpenseForm(forms.Form):
    amount = forms.DecimalField(min_value=Decimal('0.01'), decimal_places=2, max_digits=12, widget=forms.NumberInput(attrs={'inputmode': 'decimal', 'step': '0.01'}))
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    description = forms.CharField(required=False, label='Notes', widget=forms.Textarea(attrs={'rows': 2}))
