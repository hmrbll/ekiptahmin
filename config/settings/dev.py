"""Development settings — local machine only."""

import platform

from .base import *  # noqa: F401, F403
from .base import INSTALLED_APPS, MIDDLEWARE

# django-tailwind needs an explicit npm path on Windows (looks for `npm`, not `npm.cmd`)
if platform.system() == "Windows":
    NPM_BIN_PATH = r"C:\Program Files\nodejs\npm.cmd"

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# File-based email backend for dev — writes to _dev_emails/ as .eml files
# so Windows/Outlook/Thunderbird open them natively.
# Console backend would crash on Windows with Turkish chars (cp1252 stdout).
from .base import BASE_DIR  # noqa: E402

EMAIL_BACKEND = "config.email_backends.EmlFileEmailBackend"
EMAIL_FILE_PATH = BASE_DIR / "_dev_emails"
EMAIL_FILE_PATH.mkdir(exist_ok=True)

# Django Debug Toolbar
INSTALLED_APPS = [*INSTALLED_APPS, "debug_toolbar", "django_browser_reload"]
MIDDLEWARE = [
    "debug_toolbar.middleware.DebugToolbarMiddleware",
    *MIDDLEWARE,
    "django_browser_reload.middleware.BrowserReloadMiddleware",
]

# django-tailwind reload
INTERNAL_IPS = ["127.0.0.1"]

# WhiteNoise: keep autorefresh on so dev and tests behave like `runserver`.
# WhiteNoise derives autorefresh from DEBUG by default; the Django test runner
# forces DEBUG=False, which flips autorefresh off and makes the middleware
# eagerly scan STATIC_ROOT (staticfiles/) at init. That dir only exists after
# `collectstatic` (prod build), so under pytest it doesn't — emitting a
# "No directory at: .../staticfiles/" UserWarning on every request. Pinning it
# True (the real dev value anyway) removes the eager scan and the noise.
WHITENOISE_AUTOREFRESH = True
