"""Development settings — local machine only."""

import platform

from .base import *  # noqa: F401, F403
from .base import INSTALLED_APPS, MIDDLEWARE, env

# django-tailwind needs an explicit npm path on Windows (looks for `npm`, not `npm.cmd`)
if platform.system() == "Windows":
    NPM_BIN_PATH = r"C:\Program Files\nodejs\npm.cmd"

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# Console email backend for dev (prints emails to terminal)
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Django Debug Toolbar
INSTALLED_APPS = [*INSTALLED_APPS, "debug_toolbar", "django_browser_reload"]
MIDDLEWARE = [
    "debug_toolbar.middleware.DebugToolbarMiddleware",
    *MIDDLEWARE,
    "django_browser_reload.middleware.BrowserReloadMiddleware",
]

# django-tailwind reload
INTERNAL_IPS = ["127.0.0.1"]
