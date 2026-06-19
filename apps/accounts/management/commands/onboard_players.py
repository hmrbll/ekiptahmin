"""Pre-create player accounts with set nicknames and email each a one-click
onboarding link.

Unlike `send_invites` (recipient self-signs-up and picks their own nickname),
this creates the account up front with a nickname you choose, then sends a
"hesabın hazır" email whose link logs them straight in — no signup form, no
15-minute magic-link expiry. The link is a long-lived, reusable invite code
(see apps.accounts.views.invite_signup auto-login branch); whenever they click
it, they're in.

Idempotent: re-running reuses the existing account + active invite and just
re-sends. Existing accounts keep their current nickname (won't be overwritten);
inactive accounts are activated so the one-click link works.

Delivery only happens for real when RESEND_API_KEY is set (otherwise the dummy
backend drops mail). Run from Render Shell.

  python manage.py onboard_players --players "Ali:oyuncu1@gmail.com,Can:k@x.com" --dry-run
  python manage.py onboard_players --players "Ali:oyuncu1@gmail.com,Can:k@x.com"
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.utils import timezone

from apps.accounts.models import Invite
from apps.notifications.emails import send_onboarding_link
from apps.notifications.models import EmailLog


class Command(BaseCommand):
    help = "Pre-create player accounts (set nicknames) and email one-click onboarding links."

    def add_arguments(self, parser):
        parser.add_argument(
            "--players", required=True,
            help='Comma-separated "nickname:email" pairs.',
        )
        parser.add_argument(
            "--expiry-days", type=int, default=365,
            help="How long the one-click login link stays valid (default 365 — effectively no limit).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would happen without creating accounts or sending mail.",
        )

    def handle(self, *args, players, expiry_days, dry_run, **options):
        roster = self._parse(players)
        if not roster:
            raise CommandError("No players given. Pass --players \"Nick:email,...\".")

        self.stdout.write(f"EMAIL_BACKEND = {settings.EMAIL_BACKEND}")
        if "dummy" in settings.EMAIL_BACKEND.lower():
            self.stdout.write(self.style.WARNING(
                "Backend is dummy — accounts would be created but NO mail delivered. "
                "Set RESEND_API_KEY in Render env first."
            ))
        self.stdout.write(f"{len(roster)} player(s) to onboard. dry_run={dry_run} link_valid_days={expiry_days}")
        self.stdout.write("---")

        User = get_user_model()
        expires_at = timezone.now() + timedelta(days=expiry_days)
        created = existing = sent = failed = 0

        for nick, email in roster:
            try:
                validate_email(email)
            except ValidationError:
                self.stdout.write(self.style.ERROR(f"  invalid  {email}"))
                failed += 1
                continue

            if dry_run:
                self.stdout.write(f"  [dry]    {nick} <{email}> → create/reuse account + send")
                continue

            user, was_created = User.objects.get_or_create(
                email=email,
                defaults={"username": email, "nickname": nick, "is_active": True},
            )
            if was_created:
                user.set_unusable_password()
                user.save(update_fields=["password"])
                created += 1
            else:
                if not user.is_active:
                    user.is_active = True
                    user.save(update_fields=["is_active"])
                existing += 1

            invite = (
                Invite.objects
                .filter(email__iexact=email, used_at__isnull=True, expires_at__gt=timezone.now())
                .order_by("-expires_at")
                .first()
            )
            if invite is None:
                invite = Invite.objects.create(
                    email=email, note=f"onboarding: {nick}", expires_at=expires_at,
                )

            log = send_onboarding_link(user, invite)
            if log.status == EmailLog.FAILED:
                self.stdout.write(self.style.ERROR(f"  failed   {nick} <{email}>: {log.error}"))
                failed += 1
                continue

            tag = "new" if was_created else "existing"
            self.stdout.write(self.style.SUCCESS(f"  sent     {nick} <{email}> ({tag} account)"))
            sent += 1

        self.stdout.write("---")
        self.stdout.write(self.style.MIGRATE_HEADING("Summary"))
        self.stdout.write(f"  accounts created : {created}")
        self.stdout.write(f"  accounts existing: {existing}")
        self.stdout.write(f"  emails sent      : {sent}")
        self.stdout.write(f"  failed           : {failed}")

    def _parse(self, players: str):
        """Return a de-duplicated [(nickname, email)] preserving first-seen order."""
        seen = set()
        roster = []
        for chunk in players.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if ":" not in chunk:
                raise CommandError(f'Bad pair (expected "nick:email"): {chunk!r}')
            nick, email = chunk.split(":", 1)
            nick, email = nick.strip(), email.strip().lower()
            if not nick or not email:
                raise CommandError(f'Bad pair (empty nick or email): {chunk!r}')
            if email in seen:
                continue
            seen.add(email)
            roster.append((nick, email))
        return roster
