from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Invite

User = get_user_model()


@pytest.fixture
def invite(db):
    return Invite.objects.create(email="alice@example.com", note="Test invite")


@pytest.mark.django_db
class TestInviteSignup:
    def test_valid_invite_shows_form(self, client, invite):
        url = reverse("invite_signup", args=[invite.code])
        r = client.get(url)
        assert r.status_code == 200
        assert b"Kay" in r.content  # "Kayıt ol"
        # Email should be pre-filled
        assert b"alice@example.com" in r.content

    def test_expired_invite_returns_410(self, client, invite):
        invite.expires_at = timezone.now() - timedelta(days=1)
        invite.save()
        r = client.get(reverse("invite_signup", args=[invite.code]))
        assert r.status_code == 410

    def test_used_invite_returns_410(self, client, invite, django_user_model):
        user = django_user_model.objects.create(
            email="someone@x.com", username="someone@x.com"
        )
        invite.mark_used(user)
        r = client.get(reverse("invite_signup", args=[invite.code]))
        assert r.status_code == 410

    def test_unknown_invite_returns_404(self, client):
        r = client.get(reverse("invite_signup", args=["nonexistent"]))
        assert r.status_code == 404

    def test_signup_creates_inactive_user(self, client, invite):
        r = client.post(
            reverse("invite_signup", args=[invite.code]),
            {"nickname": "Alice"},
        )
        assert r.status_code == 200
        user = User.objects.get(email="alice@example.com")
        assert user.is_active is False
        assert user.nickname == "Alice"

    def test_signup_sends_magic_link_email(self, client, invite, settings):
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        client.post(
            reverse("invite_signup", args=[invite.code]),
            {"nickname": "Alice"},
        )
        assert len(mail.outbox) == 1
        assert "alice@example.com" in mail.outbox[0].to
        assert "/auth/confirm/" in mail.outbox[0].body

    def test_invite_not_consumed_until_token_confirmed(self, client, invite):
        """Form submit alone shouldn't burn the invite — protection against typos."""
        client.post(
            reverse("invite_signup", args=[invite.code]),
            {"nickname": "Alice"},
        )
        invite.refresh_from_db()
        assert invite.used_at is None

    def test_posted_email_field_is_ignored(self, client, invite):
        """Form no longer collects email from POST — invite.email is the only source."""
        client.post(
            reverse("invite_signup", args=[invite.code]),
            {"email": "different@example.com", "nickname": "Alice"},
        )
        assert User.objects.filter(email="alice@example.com").exists()
        assert not User.objects.filter(email="different@example.com").exists()

    def test_short_nickname_rejected(self, client, invite):
        r = client.post(
            reverse("invite_signup", args=[invite.code]),
            {"nickname": "A"},
        )
        assert r.status_code == 200
        assert not User.objects.filter(email="alice@example.com").exists()
