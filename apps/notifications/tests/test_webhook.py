"""Tests for the Resend bounce/complaint webhook (Faz 1.3): Svix signature
verification, user flagging, EmailLog marking, digest exclusion, and the
rejection paths (bad signature, unset secret, replay, unknown event)."""
import base64
import hashlib
import hmac
import json
import time

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.notifications import digest
from apps.notifications.models import EmailLog

WEBHOOK_URL = "notifications:resend_webhook"
SECRET = "whsec_" + base64.b64encode(b"super-secret-signing-key").decode()


@pytest.fixture
def secret(settings):
    settings.RESEND_WEBHOOK_SECRET = SECRET
    return SECRET


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(
        email="bouncer@x.com", username="bouncer@x.com", nickname="Bouncer",
    )


def _sign(secret, svix_id, ts, body: bytes) -> str:
    key = base64.b64decode(secret.split("_", 1)[1])
    signed = f"{svix_id}.{ts}.".encode() + body
    return "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


def _post(client, secret, payload, *, sign=True, ts=None):
    body = json.dumps(payload).encode()
    svix_id, ts = "msg_test", ts or str(int(time.time()))
    sig = _sign(secret, svix_id, ts, body) if sign else "v1,bogus"
    return client.post(
        reverse(WEBHOOK_URL), data=body, content_type="application/json",
        HTTP_SVIX_ID=svix_id, HTTP_SVIX_TIMESTAMP=ts, HTTP_SVIX_SIGNATURE=sig,
    )


def _payload(email, kind="email.bounced"):
    return {"type": kind, "data": {"to": [email], "email_id": "e1"}}


@pytest.mark.django_db
def test_bounce_flags_user_and_marks_log(client, secret, user):
    EmailLog.objects.create(email=user.email, kind=EmailLog.DAILY_MORNING,
                            subject="s", status=EmailLog.SENT)
    resp = _post(client, secret, _payload(user.email))
    assert resp.status_code == 200
    user.refresh_from_db()
    assert user.email_undeliverable is True
    assert user.email_undeliverable_reason == get_user_model().BOUNCE
    assert user.email_undeliverable_at is not None
    assert EmailLog.objects.filter(email=user.email, status=EmailLog.BOUNCED).exists()


@pytest.mark.django_db
def test_complaint_sets_reason(client, secret, user):
    _post(client, secret, _payload(user.email, "email.complained"))
    user.refresh_from_db()
    assert user.email_undeliverable is True
    assert user.email_undeliverable_reason == get_user_model().COMPLAINT


@pytest.mark.django_db
def test_flagged_user_excluded_from_digest(client, secret, user):
    assert user in digest.digest_recipients()
    _post(client, secret, _payload(user.email))
    assert user not in digest.digest_recipients()


@pytest.mark.django_db
def test_unknown_recipient_acked_without_error(client, secret):
    # No user with this address — must still 200 (and not crash).
    resp = _post(client, secret, _payload("ghost@x.com"))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_bad_signature_rejected(client, secret, user):
    resp = _post(client, secret, _payload(user.email), sign=False)
    assert resp.status_code == 401
    user.refresh_from_db()
    assert user.email_undeliverable is False


@pytest.mark.django_db
def test_secret_unset_rejects(client, settings, user):
    settings.RESEND_WEBHOOK_SECRET = ""
    resp = _post(client, SECRET, _payload(user.email))
    assert resp.status_code == 503
    user.refresh_from_db()
    assert user.email_undeliverable is False


@pytest.mark.django_db
def test_unknown_event_ignored(client, secret, user):
    resp = _post(client, secret, {"type": "email.delivered", "data": {"to": [user.email]}})
    assert resp.status_code == 200
    user.refresh_from_db()
    assert user.email_undeliverable is False


@pytest.mark.django_db
def test_replay_old_timestamp_rejected(client, secret, user):
    resp = _post(client, secret, _payload(user.email), ts=str(int(time.time()) - 3600))
    assert resp.status_code == 401
    user.refresh_from_db()
    assert user.email_undeliverable is False
