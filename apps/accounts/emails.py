from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from sesame.utils import get_query_string


def _confirm_url(user) -> str:
    qs = get_query_string(user)
    return f"{settings.SITE_URL}/auth/confirm/{qs}"


def send_signup_magic_link(user, *, invite=None) -> None:
    confirm_url = _confirm_url(user)
    context = {
        "nickname": user.nickname,
        "confirm_url": confirm_url,
        "invite": invite,
    }
    subject = "ekiptahmin.com — hesabını aktif et"
    body = render_to_string("emails/magic_link_signup.txt", context)
    html = render_to_string("emails/magic_link_signup.html", context)
    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html,
    )


def send_login_magic_link(user) -> None:
    confirm_url = _confirm_url(user)
    context = {"nickname": user.nickname, "confirm_url": confirm_url}
    subject = "ekiptahmin.com — giriş linkin"
    body = render_to_string("emails/magic_link_login.txt", context)
    html = render_to_string("emails/magic_link_login.html", context)
    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html,
    )
