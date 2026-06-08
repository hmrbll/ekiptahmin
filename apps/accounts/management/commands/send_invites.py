"""Bulk-create invites and send the welcome email to each address.

Mirrors what admin's Invite "add" does (creates an Invite, fires
send_invite_welcome) but for a whole list at once. Safe to re-run: an address
that already has an active invite is skipped unless --resend is passed, and an
address that already belongs to a registered user is always skipped.

Like every email path, delivery only works in prod when RESEND_API_KEY is set
(otherwise the dummy backend silently drops mail). The command prints the
active backend up front and warns if it's the dummy.

  # preview without creating/sending anything
  python manage.py send_invites --emails "a@x.com,b@y.com" --dry-run

  # create + send
  python manage.py send_invites --emails "a@x.com,b@y.com"

  # from a file (one address per line; "email" or "email,note")
  python manage.py send_invites --file invites.txt

  # re-send the welcome to addresses that already have an active invite
  python manage.py send_invites --emails "a@x.com" --resend
"""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.conf import settings
from django.utils import timezone

from apps.accounts.models import Invite
from apps.notifications.emails import send_invite_welcome


class Command(BaseCommand):
    help = "Bulk-create invites and email the welcome link to each address."

    def add_arguments(self, parser):
        parser.add_argument(
            "--emails", default="",
            help="Comma-separated email addresses.",
        )
        parser.add_argument(
            "--file", default=None,
            help="Path to a file with one address per line ('email' or 'email,note').",
        )
        parser.add_argument(
            "--note", default="",
            help="Default internal note applied to every created invite.",
        )
        parser.add_argument(
            "--resend", action="store_true",
            help="Re-send the welcome to addresses that already have an active invite.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would happen without creating invites or sending mail.",
        )

    def handle(self, *args, emails, file, note, resend, dry_run, **options):
        entries = self._collect_entries(emails, file, note)
        if not entries:
            raise CommandError("No addresses given. Pass --emails or --file.")

        self.stdout.write(f"EMAIL_BACKEND = {settings.EMAIL_BACKEND}")
        if "dummy" in settings.EMAIL_BACKEND.lower():
            self.stdout.write(self.style.WARNING(
                "Backend is dummy — invites will be created but NO mail is delivered. "
                "Set RESEND_API_KEY in Render env first."
            ))
        self.stdout.write(f"{len(entries)} address(es) to process. dry_run={dry_run} resend={resend}")
        self.stdout.write("---")

        User = get_user_model()
        created = resent = skipped_member = skipped_invited = failed = 0

        for email, row_note in entries:
            try:
                validate_email(email)
            except ValidationError:
                self.stdout.write(self.style.ERROR(f"  invalid  {email}"))
                failed += 1
                continue

            if User.objects.filter(email__iexact=email).exists():
                self.stdout.write(f"  member   {email} (already registered — skipped)")
                skipped_member += 1
                continue

            existing = (
                Invite.objects
                .filter(email__iexact=email, used_at__isnull=True, expires_at__gt=timezone.now())
                .first()
            )

            if existing and not resend:
                self.stdout.write(f"  invited  {email} (active invite exists — skipped)")
                skipped_invited += 1
                continue

            if dry_run:
                action = "resend" if existing else "create"
                self.stdout.write(f"  [dry]    {email} → would {action} + send")
                continue

            try:
                invite = existing or Invite.objects.create(email=email, note=row_note)
                send_invite_welcome(invite)
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  failed   {email}: {exc}"))
                failed += 1
                continue

            if existing:
                self.stdout.write(self.style.SUCCESS(f"  resent   {email}"))
                resent += 1
            else:
                self.stdout.write(self.style.SUCCESS(f"  sent     {email}"))
                created += 1

        self.stdout.write("---")
        self.stdout.write(self.style.MIGRATE_HEADING("Summary"))
        self.stdout.write(f"  created+sent     : {created}")
        self.stdout.write(f"  resent           : {resent}")
        self.stdout.write(f"  skipped (member) : {skipped_member}")
        self.stdout.write(f"  skipped (invited): {skipped_invited}")
        self.stdout.write(f"  failed           : {failed}")

    def _collect_entries(self, emails, file, default_note):
        """Return a de-duplicated list of (email, note) preserving first-seen order."""
        raw = []
        if emails:
            raw.extend((e, default_note) for e in emails.split(","))
        if file:
            path = Path(file)
            if not path.exists():
                raise CommandError(f"File not found: {file}")
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                parts = line.split(",", 1)
                email = parts[0]
                line_note = parts[1].strip() if len(parts) > 1 else default_note
                raw.append((email, line_note))

        seen = set()
        entries = []
        for email, note in raw:
            email = email.strip().lower()
            if not email or email in seen:
                continue
            seen.add(email)
            entries.append((email, note))
        return entries
