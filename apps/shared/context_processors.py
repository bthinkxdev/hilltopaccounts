from .periods import navbar_period


def period_nav(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    return {'nav_period': navbar_period(request)}
