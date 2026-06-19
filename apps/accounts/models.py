import secrets
from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Custom user model — keep it minimal for now, expand later."""

    email = models.EmailField(unique=True)
    nickname = models.CharField(max_length=40, blank=True)
    timezone = models.CharField(max_length=64, default="Europe/Istanbul")

    # Set by the Resend bounce/complaint webhook (Faz 1.3). When True the
    # address is dropped from digest fan-out (see notifications.digest.
    # digest_recipients). Reversible by staff in admin once the user fixes
    # their mailbox — we never auto-deactivate the account.
    BOUNCE = "bounce"
    COMPLAINT = "complaint"
    UNDELIVERABLE_REASONS = [(BOUNCE, "Bounced"), (COMPLAINT, "Complained")]
    email_undeliverable = models.BooleanField(default=False)
    email_undeliverable_at = models.DateTimeField(null=True, blank=True)
    email_undeliverable_reason = models.CharField(
        max_length=16, blank=True, choices=UNDELIVERABLE_REASONS,
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    def mark_email_undeliverable(self, reason: str) -> None:
        """Flag this address as bouncing/complained (idempotent-ish; updates
        the timestamp + reason each call)."""
        self.email_undeliverable = True
        self.email_undeliverable_at = timezone.now()
        self.email_undeliverable_reason = reason
        self.save(update_fields=[
            "email_undeliverable", "email_undeliverable_at", "email_undeliverable_reason",
        ])

    def get_short_name(self) -> str:
        # Django admin header bunu kullanır — email yerine nickname göster
        return self.nickname or self.email.split("@")[0]

    def __str__(self) -> str:
        return self.nickname or self.email


def _gen_invite_code() -> str:
    return secrets.token_urlsafe(16)


def _default_invite_expiry():
    return timezone.now() + timedelta(days=30)


class Invite(models.Model):
    code = models.CharField(max_length=32, unique=True, default=_gen_invite_code)
    email = models.EmailField(blank=True, help_text="Optional — pre-fills the sign-up form for this address.")
    note = models.CharField(max_length=200, blank=True, help_text="Internal note: who this invite is for.")

    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invites_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=_default_invite_expiry)

    used_at = models.DateTimeField(null=True, blank=True)
    used_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invite_used",
    )

    class Meta:
        ordering = ("-created_at",)

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and timezone.now() < self.expires_at

    @property
    def status(self) -> str:
        if self.used_at:
            return "used"
        if timezone.now() >= self.expires_at:
            return "expired"
        return "active"

    def mark_used(self, user) -> None:
        self.used_at = timezone.now()
        self.used_by = user
        self.save(update_fields=["used_at", "used_by"])

    def __str__(self) -> str:
        label = self.note or self.email or self.code[:8]
        return f"{label} — {self.status}"
