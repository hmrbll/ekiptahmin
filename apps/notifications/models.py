"""Notification persistence: a log of every scheduled/lifecycle email we send.

`EmailLog` serves two jobs at once:

1. **Audit trail** — one row per recipient per send (who, what, when, status).
   Powers the planned staff `/ops/emails/` tracking page (Faz 1.2).
2. **Digest dedup key** — the daily-digest cron runs the evening job hourly
   (08:00–12:00 TRT) and the morning job once. `slate_date` + `kind` let a run
   ask "did we already send this slate's digest?" so the hourly evening poll
   sends exactly once. See `digest_already_sent`.
"""
from django.conf import settings
from django.db import models


class EmailLog(models.Model):
    # What kind of mail this was. Daily digests are the only ones written here
    # today; the auth/invite kinds are reserved so those senders can log too.
    DAILY_MORNING = "daily_morning"
    DAILY_EVENING = "daily_evening"
    INVITE_WELCOME = "invite_welcome"
    ONBOARDING = "onboarding"
    MAGIC_LINK_SIGNUP = "magic_link_signup"
    MAGIC_LINK_LOGIN = "magic_link_login"
    KIND_CHOICES = [
        (DAILY_MORNING, "Daily — sabah"),
        (DAILY_EVENING, "Daily — akşam"),
        (INVITE_WELCOME, "Davet — hoş geldin"),
        (ONBOARDING, "Onboarding"),
        (MAGIC_LINK_SIGNUP, "Magic link — kayıt"),
        (MAGIC_LINK_LOGIN, "Magic link — giriş"),
    ]

    # Delivery outcome as seen at send time. NOTE: SENT only means the backend
    # accepted it (mirrors apps.notifications.emails._send semantics) — not
    # proof of inbox delivery. DROPPED = dummy backend (RESEND_API_KEY unset).
    SENT = "sent"
    DROPPED = "dropped"
    REJECTED = "rejected"
    FAILED = "failed"
    # Set asynchronously by the Resend webhook (Faz 1.3), after the original
    # send — not an outcome we can know at send time.
    BOUNCED = "bounced"
    COMPLAINED = "complained"
    STATUS_CHOICES = [
        (SENT, "Gönderildi"),
        (DROPPED, "Düştü (dummy backend)"),
        (REJECTED, "Reddedildi"),
        (FAILED, "Hata"),
        (BOUNCED, "Geri döndü (bounce)"),
        (COMPLAINED, "Şikayet (spam)"),
    ]
    # Statuses that count as "this digest slate has been handled" for dedup.
    HANDLED_STATUSES = (SENT, DROPPED)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="email_logs",
    )
    email = models.EmailField(help_text="Recipient address, kept even if the user is later deleted.")
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    subject = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    # The digest slate this mail belongs to (the date the 13:00→13:00 window
    # opens). Null for non-digest mails. Drives the dedup query.
    slate_date = models.DateField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["kind", "slate_date"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.kind} → {self.email} [{self.status}]"

    @classmethod
    def digest_already_sent(cls, kind: str, slate_date) -> bool:
        """True if this digest slate has already been handled (sent/dropped to
        at least one recipient). A run that only produced FAILED rows is NOT
        considered handled, so the next hourly poll can retry it."""
        return cls.objects.filter(
            kind=kind, slate_date=slate_date, status__in=cls.HANDLED_STATUSES,
        ).exists()

    @classmethod
    def mark_latest_undeliverable(cls, email: str, status: str):
        """Flip the most recent logged mail to `email` to a bounced/complained
        status, so the bounce is visible on /ops/emails/. Returns the row (or
        None if we never logged a mail to that address)."""
        log = cls.objects.filter(email__iexact=email).order_by("-created_at").first()
        if log is not None:
            log.status = status
            log.save(update_fields=["status"])
        return log
