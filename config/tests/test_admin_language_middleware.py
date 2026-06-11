"""AdminLanguageMiddleware — English only under /admin/, Turkish everywhere
else, and no thread-local leakage between requests.

Regression context: the original middleware activated "en" for /admin/ and
never reset it. Worker threads are reused, so any public request served after
an admin request on the same thread rendered English month/day names.
"""

from django.utils import translation

from config.middleware import AdminLanguageMiddleware


class _DummyRequest:
    def __init__(self, path: str):
        self.path = path


def _middleware_capturing(languages: list):
    def get_response(request):
        languages.append(translation.get_language())
        return "response"
    return AdminLanguageMiddleware(get_response)


def test_admin_request_renders_english():
    seen: list = []
    mw = _middleware_capturing(seen)
    mw(_DummyRequest("/admin/tournament/stage/"))
    assert seen == ["en"]


def test_public_request_after_admin_request_renders_turkish():
    seen: list = []
    mw = _middleware_capturing(seen)
    mw(_DummyRequest("/admin/"))
    mw(_DummyRequest("/tahminler/"))  # same thread, as in a reused worker
    assert seen == ["en", "tr"]


def test_active_language_resets_after_admin_response():
    mw = _middleware_capturing([])
    mw(_DummyRequest("/admin/"))
    # deactivate() in finally restores the settings default for the thread.
    assert translation.get_language() == "tr"


def test_request_language_code_attribute_set():
    mw = _middleware_capturing([])
    admin_req = _DummyRequest("/admin/")
    public_req = _DummyRequest("/")
    mw(admin_req)
    mw(public_req)
    assert admin_req.LANGUAGE_CODE == "en"
    assert public_req.LANGUAGE_CODE == "tr"
