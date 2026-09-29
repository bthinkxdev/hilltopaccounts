from django import template
register = template.Library()

@register.filter
def urlencode_without_page(get_params):
    qd = get_params.copy()
    qd.pop('page', None)
    return qd.urlencode()
