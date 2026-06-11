from django.conf import settings
from django.utils import translation


class AdminLanguageMiddleware:
    """Activate English for /admin/ URLs; everything else renders in the
    site default language (Turkish).

    The active language is stored thread-local and worker threads are reused
    across requests, so BOTH branches must activate explicitly — activating
    only for /admin/ leaves English sticking to the thread, and later public
    requests served by it render English month/day names in `|date` output.
    `deactivate()` in `finally` resets the thread to the settings default
    even if the view raises.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = "en" if request.path.startswith("/admin/") else settings.LANGUAGE_CODE
        translation.activate(language)
        request.LANGUAGE_CODE = language
        try:
            return self.get_response(request)
        finally:
            translation.deactivate()
