from django.conf import settings
from django.template.loader import render_to_string
from sesame.utils import get_query_string

from apps.notifications.emails import send_logged


def _confirm_url(user) -> str:
    qs = get_query_string(user)
    return f"{settings.SITE_URL}/auth/confirm/{qs}"


def send_signup_magic_link(user, *, invite=None):
    """Magic-link activation mail.

    Routed through `send_logged` so it shows up in the /ops/emails/ audit and
    never raises — a hard SMTP error becomes a FAILED EmailLog row rather than
    a 500 on the sign-up form (the account is already created at this point;
    the user can re-request a link from the login page). Returns the row.

    `send_logged` attaches the Reply-To header (smtp deliverability) for us.
    """
    from apps.notifications.models import EmailLog

    confirm_url = _confirm_url(user)
    context = {
        "nickname": user.nickname,
        "confirm_url": confirm_url,
        "invite": invite,
        "site_url": settings.SITE_URL,
    }
    return send_logged(
        subject="ekiptahmin.com — hesabını aktif et",
        body=render_to_string("emails/magic_link_signup.txt", context),
        html=render_to_string("emails/magic_link_signup.html", context),
        recipient=user.email,
        kind=EmailLog.MAGIC_LINK_SIGNUP,
        user=user,
    )


def send_login_magic_link(user):
    """Magic-link login mail. See send_signup_magic_link for the logging /
    never-raises contract. Returns the EmailLog row."""
    from apps.notifications.models import EmailLog

    confirm_url = _confirm_url(user)
    context = {
        "nickname": user.nickname,
        "confirm_url": confirm_url,
        "site_url": settings.SITE_URL,
    }
    return send_logged(
        subject="ekiptahmin.com — giriş linkin",
        body=render_to_string("emails/magic_link_login.txt", context),
        html=render_to_string("emails/magic_link_login.html", context),
        recipient=user.email,
        kind=EmailLog.MAGIC_LINK_LOGIN,
        user=user,
    )
