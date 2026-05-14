import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from sesame.utils import get_query_string

logger = logging.getLogger(__name__)


def _confirm_url(user) -> str:
    qs = get_query_string(user)
    return f"{settings.SITE_URL}/auth/confirm/{qs}"


def _send(subject: str, body: str, html: str, recipient: str) -> None:
    """Send a transactional email, surfacing failures to logs.

    The dummy backend (used when RESEND_API_KEY is unset) returns 1 here too,
    so a successful return is NOT proof of delivery. The startup-time warning
    in prod settings + the explicit log line below is what makes silent drops
    visible.
    """
    backend = settings.EMAIL_BACKEND
    try:
        accepted = send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            html_message=html,
            fail_silently=False,
        )
    except Exception:
        logger.exception("mail.failed to=%s subject=%r backend=%s", recipient, subject, backend)
        raise
    if "dummy" in backend.lower():
        logger.warning("mail.dropped to=%s subject=%r (dummy backend)", recipient, subject)
    elif accepted:
        logger.info("mail.sent to=%s subject=%r", recipient, subject)
    else:
        logger.warning("mail.rejected to=%s subject=%r", recipient, subject)


def send_signup_magic_link(user, *, invite=None) -> None:
    confirm_url = _confirm_url(user)
    context = {
        "nickname": user.nickname,
        "confirm_url": confirm_url,
        "invite": invite,
    }
    _send(
        subject="ekiptahmin.com — hesabını aktif et",
        body=render_to_string("emails/magic_link_signup.txt", context),
        html=render_to_string("emails/magic_link_signup.html", context),
        recipient=user.email,
    )


def send_login_magic_link(user) -> None:
    confirm_url = _confirm_url(user)
    context = {"nickname": user.nickname, "confirm_url": confirm_url}
    _send(
        subject="ekiptahmin.com — giriş linkin",
        body=render_to_string("emails/magic_link_login.txt", context),
        html=render_to_string("emails/magic_link_login.html", context),
        recipient=user.email,
    )
