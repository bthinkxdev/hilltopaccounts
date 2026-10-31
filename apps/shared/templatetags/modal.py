from django import template
from django.utils.safestring import mark_safe
from apps.shared.modal import is_action_url

register = template.Library()


@register.filter
def modal_attr(url):
    """` data-modal` for an address that leads to an action page, so the link opens as a popup."""
    return mark_safe(' data-modal') if is_action_url(url) else ''
