"""Which pages are action pages (a form or a confirmation) and so open as a popup.

Templates mark their own `{% url %}` links with `data-modal`. A link that arrives as a ready-made address (an alert, a
dashboard card) is checked here against the same list. `tests_modal` compares this list with the routes that really
render an action template, so a new action page cannot be forgotten.
"""
from django.urls import Resolver404, resolve

ACTION_URL_NAMES = frozenset("""
accounts:assignment_create accounts:assignment_remove accounts:password_change accounts:staff_create
accounts:staff_deactivate accounts:staff_password_change accounts:staff_whatsapp_edit
billing:charge_cancel billing:charge_create billing:invoice_cancel billing:invoice_generate billing:payment_cancel
billing:payment_correct billing:payment_record
businesses:archive businesses:create businesses:partner_create businesses:partner_edit businesses:partner_payout_correct
businesses:partner_payout_create businesses:partner_payout_void businesses:partner_share_create
businesses:villa_partner_payout_batch
cash:handover_confirm cash:handover_reject cash:handover_submit
expenses:cancel expenses:create expenses:create_for_villa expenses:mark_paid expenses:recurring_create
expenses:recurring_edit expenses:recurring_pay_ahead expenses:recurring_stop expenses:reverse expenses:revise
tenancy:create tenancy:deposit_correct tenancy:deposit_edit tenancy:deposit_void tenancy:edit tenancy:move_out
villas:partition_archive villas:partition_create villas:partition_edit villas:partition_photo_add villas:photo_delete
villas:villa_archive villas:villa_create villas:villa_edit villas:villa_photo_add villas:villa_sell
""".split())

ACTION_TEMPLATES = ('components/form_page.html', 'components/confirm_reason.html', 'accounts/staff_create.html', 'businesses/villa_payout_batch.html')


def is_action_url(url) -> bool:
    path = str(url or '').split('?', 1)[0].split('#', 1)[0]
    if not path.startswith('/'):
        return False
    try:
        match = resolve(path)
    except Resolver404:
        return False
    return f'{match.namespace}:{match.url_name}' in ACTION_URL_NAMES
