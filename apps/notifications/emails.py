"""Scheduled / lifecycle emails: invite welcome, round openings, daily digests.

Transactional auth emails (magic-link signup/login) live in apps.accounts.emails.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _send(subject: str, body: str, html: str, recipient: str) -> None:
    backend = settings.EMAIL_BACKEND
    msg = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
        reply_to=[settings.REPLY_TO_EMAIL],
    )
    msg.attach_alternative(html, "text/html")
    try:
        accepted = msg.send(fail_silently=False)
    except Exception:
        logger.exception("mail.failed to=%s subject=%r backend=%s", recipient, subject, backend)
        raise
    if "dummy" in backend.lower():
        logger.warning("mail.dropped to=%s subject=%r (dummy backend)", recipient, subject)
    elif accepted:
        logger.info("mail.sent to=%s subject=%r", recipient, subject)
    else:
        logger.warning("mail.rejected to=%s subject=%r", recipient, subject)


def send_invite_welcome(invite) -> None:
    """Sent when Hemre creates an Invite in admin. Carries the invite link
    (not a magic link) — the recipient still completes a minimal form
    (nickname only) so they choose their own display name."""
    invite_url = f"{settings.SITE_URL}/invite/{invite.code}/"
    context = {
        "invite_url": invite_url,
        "invite": invite,
        "site_url": settings.SITE_URL,
    }
    _send(
        subject="ekiptahmin.com — davetiyen geldi",
        body=render_to_string("emails/invite_welcome.txt", context),
        html=render_to_string("emails/invite_welcome.html", context),
        recipient=invite.email,
    )
