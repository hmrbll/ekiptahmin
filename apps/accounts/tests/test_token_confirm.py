import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from sesame.utils import get_query_string

from apps.accounts.models import Invite

User = get_user_model()


def _confirm_url_for(user):
    return f"{reverse('confirm_token')}{get_query_string(user)}"


@pytest.mark.django_db
class TestTokenConfirm:
    def test_invalid_token_returns_410(self, client):
        r = client.get(f"{reverse('confirm_token')}?t=invalid_token_xyz")
        assert r.status_code == 410

    def test_missing_token_returns_410(self, client):
        r = client.get(reverse("confirm_token"))
        assert r.status_code == 410

    def test_active_user_token_logs_in(self, client):
        user = User.objects.create(
            email="bob@example.com", username="bob@example.com",
            nickname="Bob", is_active=True,
        )
        r = client.get(_confirm_url_for(user))
        assert r.status_code == 302
        assert r.url.startswith(reverse("dashboard"))
        assert "event=login" in r.url
        # Session should now have the user
        assert int(client.session["_auth_user_id"]) == user.pk

    def test_inactive_user_token_activates_and_logs_in(self, client):
        """Sign-up confirmation: inactive → active + login."""
        user = User.objects.create(
            email="charlie@example.com", username="charlie@example.com",
            nickname="Charlie", is_active=False,
        )
        r = client.get(_confirm_url_for(user))
        assert r.status_code == 302
        assert "event=sign_up" in r.url
        user.refresh_from_db()
        assert user.is_active is True

    def test_inactive_user_consumes_invite(self, client):
        """Sign-up confirmation should mark the matching invite as used."""
        invite = Invite.objects.create(email="charlie@example.com", note="test")
        user = User.objects.create(
            email="charlie@example.com", username="charlie@example.com",
            nickname="Charlie", is_active=False,
        )
        r = client.get(_confirm_url_for(user))
        assert r.status_code == 302
        invite.refresh_from_db()
        assert invite.used_at is not None
        assert invite.used_by == user
