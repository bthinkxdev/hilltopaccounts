from .periods import navbar_period


def period_nav(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    return {'nav_period': navbar_period(request)}


def modal_request(request):
    """Action pages extend this: the app shell normally, a bare fragment when the popup script asks for the page."""
    modal = request.headers.get('X-Modal') == '1'
    return {'base_template': 'base_modal.html' if modal else 'base.html', 'is_modal': modal}
