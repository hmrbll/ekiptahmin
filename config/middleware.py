from django.utils import translation


class AdminLanguageMiddleware:
    """Activate English for /admin/ URLs only; rest stays Turkish."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/"):
            translation.activate("en")
            request.LANGUAGE_CODE = "en"
        return self.get_response(request)
