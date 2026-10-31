from django.http import HttpResponse
from django.utils.cache import add_never_cache_headers, patch_vary_headers

REDIRECTS = (301, 302, 303, 307, 308)


class ModalMiddleware:
    """Lets any action page open as a popup without changing the view that serves it.

    The popup script asks for a page with the `X-Modal: 1` header. The same view runs, with the same form, validation
    and service call. Only two things differ for such a request:
      * the page is rendered without the app shell (see `base_modal.html`), and
      * a redirect is not followed: it is handed back in `X-Modal-Redirect`, so the flash message the view set is still
        waiting for the page the popup then navigates to.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        is_modal = request.headers.get('X-Modal') == '1'
        response = self.get_response(request)
        patch_vary_headers(response, ['X-Modal'])
        if not is_modal:
            return response
        if response.status_code in REDIRECTS and response.has_header('Location'):
            handed_back = HttpResponse(status=200)
            handed_back['X-Modal-Redirect'] = response['Location']
            handed_back.cookies = response.cookies
            response = handed_back
            patch_vary_headers(response, ['X-Modal'])
        add_never_cache_headers(response)
        return response
