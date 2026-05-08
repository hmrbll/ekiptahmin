import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse

User = get_user_model()


@pytest.fixture
def active_user(db):
    return User.objects.create(
        email="bob@example.com",
        username="bob@example.com",
        nickname="Bob",
        is_active=True,
    )


@pytest.fixture
def inactive_user(db):
    return User.objects.create(
        email="charlie@example.com",
        username="charlie@example.com",
        nickname="Charlie",
        is_active=False,
    )


@pytest.mark.django_db
class TestLogin:
    def test_login_page_loads(self, client):
        r = client.get(reverse("login"))
        assert r.status_code == 200
        assert b"Email" in r.content

    def test_login_request_sends_magic_link_to_active_user(self, client, active_user, settings):
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        r = client.post(reverse("login"), {"email": "bob@example.com"})
        assert r.status_code == 200
        assert len(mail.outbox) == 1
        assert "bob@example.com" in mail.outbox[0].to

    def test_unknown_email_does_not_leak(self, client, settings):
        """Email enumeration koruması: bilinmeyen mailde de 'check email' sayfası."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        r = client.post(reverse("login"), {"email": "nobody@example.com"})
        assert r.status_code == 200
        # Same page as success — no leak
        assert b"Mailini kontrol et" in r.content
        # No email actually sent
        assert len(mail.outbox) == 0

    def test_inactive_user_does_not_get_login_link(self, client, inactive_user, settings):
        """Inactive user (sign-up'ı tamamlamamış) login linki almamalı."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        r = client.post(reverse("login"), {"email": "charlie@example.com"})
        assert r.status_code == 200
        # No actual email sent for inactive user
        assert len(mail.outbox) == 0
