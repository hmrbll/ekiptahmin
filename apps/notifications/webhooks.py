"""Resend (Svix-signed) bounce/complaint webhook — Faz 1.3.

Resend signs webhooks with Svix. We verify the signature manually (no svix
dependency): HMAC-SHA256 over ``<id>.<timestamp>.<body>`` keyed by the secret,
base64-compared in constant time, with a 5-minute replay window.

On ``email.bounced`` / ``email.complained`` we flag the recipient User
(reversible by staff, never auto-deactivated) so the address drops out of
digest fan-out (``digest.digest_recipients``), and mark their most recent
EmailLog row so the bounce is visible on /ops/emails/.

If ``RESEND_WEBHOOK_SECRET`` is unset we reject everything — an unsigned,
publicly reachable endpoint that flags users would be abusable.
"""
import base64
import hashlib
import hmac
import json
import logging
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import EmailLog

logger = logging.getLogger(__name__)

# Reject webhooks whose timestamp is outside this window (replay guard).
WEBHOOK_TOLERANCE_SECONDS = 5 * 60


def verify_svix_signature(secret: str, headers, body: bytes) -> bool:
    """Verify the Svix signature Resend attaches to each webhook.

    `headers` is request.headers (case-insensitive). `body` is the raw bytes.
    """
    svix_id = headers.get("svix-id", "")
    svix_timestamp = headers.get("svix-timestamp", "")
    svix_signature = headers.get("svix-signature", "")
    if not (svix_id and svix_timestamp and svix_signature):
        return False

    try:
        ts = int(svix_timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > WEBHOOK_TOLERANCE_SECONDS:
        return False

    # Secret is "whsec_<base64>"; the bytes after the prefix are the HMAC key.
    secret_b64 = secret.split("_", 1)[1] if "_" in secret else secret
    try:
        key = base64.b64decode(secret_b64)
    except Exception:
        return False

    signed = f"{svix_id}.{svix_timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    # The header may carry several space-separated "v1,<sig>" entries.
    for part in svix_signature.split():
        _, _, sig = part.partition(",")
        if sig and hmac.compare_digest(sig, expected):
            return True
    return False


@csrf_exempt
@require_POST
def resend_webhook(request):
    secret = settings.RESEND_WEBHOOK_SECRET
    if not secret:
        logger.warning("resend.webhook RESEND_WEBHOOK_SECRET unset — rejecting")
        return HttpResponse(status=503)

    body = request.body
    if not verify_svix_signature(secret, request.headers, body):
        logger.warning("resend.webhook signature verification failed")
        return HttpResponse(status=401)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest("invalid json")

    User = get_user_model()
    reason = {
        "email.bounced": User.BOUNCE,
        "email.complained": User.COMPLAINT,
    }.get(payload.get("type", ""))
    if reason is None:
        return HttpResponse(status=200)  # ack and ignore other event types

    status = EmailLog.BOUNCED if reason == User.BOUNCE else EmailLog.COMPLAINED
    recipients = payload.get("data", {}).get("to", [])
    if isinstance(recipients, str):
        recipients = [recipients]

    flagged = 0
    for addr in recipients:
        user = User.objects.filter(email__iexact=addr).first()
        if user is not None and not user.email_undeliverable:
            user.mark_email_undeliverable(reason)
            flagged += 1
        EmailLog.mark_latest_undeliverable(addr, status)

    logger.info("resend.webhook %s flagged=%d to=%s", payload.get("type"), flagged, recipients)
    return HttpResponse(status=200)
