"""Production settings — Render."""

from .base import *  # noqa: F401, F403
from .base import env

DEBUG = False

ALLOWED_HOSTS = env("ALLOWED_HOSTS")  # comma-separated in env

# Security
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31_536_000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

CSRF_TRUSTED_ORIGINS = [
    f"https://*{host}" if host.startswith(".") else f"https://{host}"
    for host in ALLOWED_HOSTS
    if host
]

# Email backend — Resend via SMTP if key is set, otherwise dummy (drops emails).
# Sign-up flow needs this; set RESEND_API_KEY in Render dashboard to enable.
_resend_key = env("RESEND_API_KEY", default="")
if _resend_key and _resend_key != "placeholder_will_set_later":
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = "smtp.resend.com"
    EMAIL_PORT = 587
    EMAIL_USE_TLS = True
    EMAIL_HOST_USER = "resend"
    EMAIL_HOST_PASSWORD = _resend_key
else:
    # No key — silently drop emails (avoids 500 on prod sign-up forms) but
    # print to stderr so Render logs make it obvious mail is disabled.
    EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
    import sys

    print(
        "WARNING: RESEND_API_KEY not set — emails are being silently dropped. "
        "Set the env var on Render to enable real delivery.",
        file=sys.stderr,
        flush=True,
    )

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}

# Error monitoring — Sentry. No-op unless SENTRY_DSN is set, so the web service
# and both digest crons stay quiet until Hemre creates a project and sets the
# DSN in Render. Errors only (no performance tracing) to stay within the free
# tier. The Django integration is auto-enabled by the SDK. RENDER_GIT_COMMIT is
# injected by Render, so events are tagged with the deployed release.
SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=env("SENTRY_ENVIRONMENT", default="production"),
        release=env("RENDER_GIT_COMMIT", default=None),
        traces_sample_rate=0,
        send_default_pii=False,
    )
