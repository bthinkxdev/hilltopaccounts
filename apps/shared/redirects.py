from django.utils.http import url_has_allowed_host_and_scheme


def safe_next(request, target, default):
    """Where to go back to after an action: the page the user came from when it is on this site, else `default`.
    Never an outside address (open-redirect safe)."""
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return default
