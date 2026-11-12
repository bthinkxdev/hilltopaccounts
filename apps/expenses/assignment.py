"""Who is going to pay an expense — Admin or Staff. One definition used by every form and rule.

This is the assignment (decided up front, by the Admin). It is not `paid_by`, which records who actually paid."""
import re
from django import forms
from apps.accounts.selectors import LANDLORD_RENT_REGEX
from .models import AssignedTo


def assignment_field(label='Assigned to'):
    """A single-choice Admin / Staff selector."""
    return forms.ChoiceField(required=False, choices=AssignedTo.choices, initial=AssignedTo.STAFF, widget=forms.RadioSelect, label=label,
                             help_text='Who is going to pay it. Staff can only view an expense assigned to the Admin.')


def keep_for_admin(form, can_assign, names):
    """Only the Admin chooses; everyone else's form simply does not have the selector."""
    if not can_assign:
        for name in names:
            form.fields.pop(name, None)


def valid(value):
    """The chosen side, or None when nothing (or something unknown) was sent."""
    return value if value in AssignedTo.values else None


def default_for(*, category_name='', auto_debit=False):
    """When nobody chose: Staff, except what only the Admin can settle (auto-debit, landlord rent)."""
    if auto_debit or re.search(LANDLORD_RENT_REGEX, category_name or '', re.I):
        return AssignedTo.OWNER
    return AssignedTo.STAFF
