"""Tests for the /ops/emails/ audit page (Faz 1.2) and the send_logged audit
trail that now covers invite, onboarding, and magic-link mails (full audit).

The list view is staff-only; the logging tests assert every lifecycle sender
writes exactly one EmailLog row with the right kind/status and never raises.
"""
import pytest
from django.contrib.auth import get_user_model
from django.core.mail.backends.base import BaseEmailBackend
from django.urls import reverse

from apps.accounts.emails import send_login_magic_link, send_signup_magic_link
from apps.accounts.models import Invite
from apps.notifications.emails import send_invite_welcome, send_onboarding_link
from apps.notifications.models import EmailLog

LIST_URL = "notifications:email_log_list"


class BoomBackend(BaseEmailBackend):
    """Email backend that always raises — to exercise the FAILED path."""

    def send_messages(self, messages):
        raise RuntimeError("smtp exploded")


@pytest.fixture
def _locmem(settings):
    """In-memory backend: no .eml files, status resolves to SENT."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user(
        email="staff@x.com", username="staff@x.com", nickname="Staff", is_staff=True,
    )


@pytest.fixture
def member(db):
    return get_user_model().objects.create_user(
        email="m@x.com", username="m@x.com", nickname="Member",
    )


def _log(email="x@x.com", *, kind=EmailLog.DAILY_MORNING, status=EmailLog.SENT, subject="s"):
    return EmailLog.objects.create(email=email, kind=kind, subject=subject, status=status)


# ----------------------------------------------------------- access control


@pytest.mark.django_db
def test_list_anonymous_redirected(client):
    assert client.get(reverse(LIST_URL)).status_code == 302


@pytest.mark.django_db
def test_list_non_staff_redirected(client, member):
    client.force_login(member)
    assert client.get(reverse(LIST_URL)).status_code == 302


@pytest.mark.django_db
def test_list_staff_ok(client, staff):
    client.force_login(staff)
    assert client.get(reverse(LIST_URL)).status_code == 200


# ----------------------------------------------------------- listing/filters


@pytest.mark.django_db
def test_list_shows_rows(client, staff):
    _log(email="alice@x.com")
    _log(email="bob@x.com")
    client.force_login(staff)
    body = client.get(reverse(LIST_URL)).content.decode()
    assert "alice@x.com" in body
    assert "bob@x.com" in body


@pytest.mark.django_db
def test_list_filters_by_status(client, staff):
    _log(email="ok@x.com", status=EmailLog.SENT)
    _log(email="bad@x.com", status=EmailLog.FAILED)
    client.force_login(staff)
    body = client.get(reverse(LIST_URL), {"status": EmailLog.FAILED}).content.decode()
    assert "bad@x.com" in body
    assert "ok@x.com" not in body


@pytest.mark.django_db
def test_list_filters_by_kind(client, staff):
    _log(email="d@x.com", kind=EmailLog.DAILY_MORNING)
    _log(email="i@x.com", kind=EmailLog.INVITE_WELCOME)
    client.force_login(staff)
    body = client.get(reverse(LIST_URL), {"kind": EmailLog.INVITE_WELCOME}).content.decode()
    assert "i@x.com" in body
    assert "d@x.com" not in body


@pytest.mark.django_db
def test_list_paginates(client, staff):
    for i in range(55):
        _log(email=f"u{i}@x.com")
    client.force_login(staff)
    page1 = client.get(reverse(LIST_URL)).context["page"]
    assert page1.paginator.num_pages == 2
    assert len(page1.object_list) == 50
    page2 = client.get(reverse(LIST_URL), {"page": 2}).context["page"]
    assert len(page2.object_list) == 5


# --------------------------------------------------- audit trail (full audit)


@pytest.mark.django_db
def test_invite_welcome_logs(_locmem):
    invite = Invite.objects.create(email="invitee@x.com", note="test")
    log = send_invite_welcome(invite)
    assert (log.kind, log.status, log.email) == (
        EmailLog.INVITE_WELCOME, EmailLog.SENT, "invitee@x.com",
    )
    assert EmailLog.objects.filter(kind=EmailLog.INVITE_WELCOME).count() == 1


@pytest.mark.django_db
def test_onboarding_logs(_locmem):
    user = get_user_model().objects.create_user(
        email="onb@x.com", username="onb@x.com", nickname="Onb",
    )
    log = send_onboarding_link(user, Invite.objects.create(email="onb@x.com"))
    assert (log.kind, log.status, log.user_id) == (EmailLog.ONBOARDING, EmailLog.SENT, user.id)


@pytest.mark.django_db
def test_magic_link_signup_logs(_locmem):
    user = get_user_model().objects.create_user(
        email="sign@x.com", username="sign@x.com", nickname="Sign",
    )
    log = send_signup_magic_link(user)
    assert (log.kind, log.status, log.user_id) == (EmailLog.MAGIC_LINK_SIGNUP, EmailLog.SENT, user.id)


@pytest.mark.django_db
def test_magic_link_login_logs(_locmem):
    user = get_user_model().objects.create_user(
        email="login@x.com", username="login@x.com", nickname="Login",
    )
    log = send_login_magic_link(user)
    assert (log.kind, log.status) == (EmailLog.MAGIC_LINK_LOGIN, EmailLog.SENT)


@pytest.mark.django_db
def test_sender_failure_is_logged_not_raised(settings):
    settings.EMAIL_BACKEND = "apps.notifications.tests.test_email_log.BoomBackend"
    user = get_user_model().objects.create_user(
        email="boom@x.com", username="boom@x.com", nickname="Boom",
    )
    log = send_login_magic_link(user)  # must not raise
    assert log.status == EmailLog.FAILED
    assert log.error
