from . import context

class CurrentRequestMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        context.set_context(request)
        try:
            return self.get_response(request)
        finally:
            context.clear_context()
