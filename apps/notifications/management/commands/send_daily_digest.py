"""Send a daily digest email — morning predictions or evening results.

The "morning"/"evening" names describe the slate role (preview vs recap), NOT
the send clock — they're intentionally inverted vs wall time. The morning
*preview* goes out at 13:00 (when the slate opens); the evening *recap* goes out
the next morning 08:00–12:00, because WC 2026 kickoffs land overnight TRT
(≈20:00–05:00) so a slate only finishes in the small hours. By design — see
docs/email_setup.md ("Daily digest cron").

Scheduling (Render cron, UTC → Europe/Istanbul):
  morning : `0 10 * * *`   (13:00 TRT) — once a day
  evening : `0 5-9 * * *`   (08:00–12:00 TRT) — hourly poll

Evening polls hourly until the previous slate's results are all in, sends once
(dedup via EmailLog), and at the 12:00 poll sends anyway with whatever's in
("sonuç bekleniyor" for the rest). See apps/notifications/digest.py for windows.

    python manage.py send_daily_digest --mode morning
    python manage.py send_daily_digest --mode evening --dry-run
    python manage.py send_daily_digest --mode evening --date 2026-06-18 --force
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.template.loader import render_to_string

from apps.notifications import digest
from apps.notifications.emails import send_logged
from apps.notifications.models import EmailLog


class Command(BaseCommand):
    help = "Send the daily digest email (morning predictions or evening results)."

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=["morning", "evening"], required=True)
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Render every recipient's mail but don't send or log.",
        )
        parser.add_argument(
            "--date", help="Override the slate date (YYYY-MM-DD), for testing.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Send now, ignoring the dedup guard and the evening "
                 "results-incomplete wait.",
        )

    def handle(self, *args, **opts):
        tournament = digest.active_tournament()
        if tournament is None:
            self.stdout.write("No active tournament — nothing to send.")
            return

        mode = opts["mode"]
        if opts["date"]:
            try:
                slate_date = datetime.strptime(opts["date"], "%Y-%m-%d").date()
            except ValueError:
                raise CommandError("--date must be YYYY-MM-DD")
        elif mode == "morning":
            slate_date = digest.morning_slate_date()
        else:
            slate_date = digest.evening_slate_date()

        if mode == "morning":
            self._morning(tournament, slate_date, opts["dry_run"], opts["force"])
        else:
            self._evening(tournament, slate_date, opts["dry_run"], opts["force"])

    # ---- morning ----

    def _morning(self, tournament, slate_date, dry, force):
        kind = EmailLog.DAILY_MORNING
        if not dry and not force and EmailLog.digest_already_sent(kind, slate_date):
            self.stdout.write(f"Morning digest for {slate_date} already sent — skipping.")
            return

        matches = digest.build_morning_matches(tournament, slate_date)
        if not matches:
            self.stdout.write(f"No public matches in slate {slate_date} — nothing to send.")
            return

        recipients = digest.digest_recipients()
        self.stdout.write(
            f"Morning digest slate {slate_date}: {len(matches)} matches "
            f"→ {len(recipients)} recipients{' (dry-run)' if dry else ''}"
        )
        self._fan_out(
            recipients, kind, slate_date, dry,
            subject=digest.morning_subject(),
            template="emails/daily_morning.html",
            build_ctx=lambda u: digest.morning_context(tournament, slate_date, u, matches=matches),
            text_fn=digest.morning_text,
        )

    # ---- evening ----

    def _evening(self, tournament, slate_date, dry, force):
        kind = EmailLog.DAILY_EVENING
        if not dry and not force and EmailLog.digest_already_sent(kind, slate_date):
            self.stdout.write(f"Evening digest for {slate_date} already sent — skipping.")
            return

        complete = digest.slate_is_complete(tournament, slate_date)
        if complete is None:
            self.stdout.write(f"No matches in slate {slate_date} — nothing to report.")
            return

        final_poll = digest.is_evening_final_poll()
        if not complete and not final_poll and not force:
            self.stdout.write(
                f"Slate {slate_date} results incomplete and not the 12:00 poll yet "
                f"— waiting for the next hour."
            )
            return

        matches = digest.build_evening_matches(tournament, slate_date)
        lb_base, daily_by_user = digest.build_evening_leaderboard(tournament, slate_date)
        recipients = digest.digest_recipients()
        pending = sum(1 for m in matches if m["pending"])
        note = "complete" if complete else f"PARTIAL — {pending} maç sonuç bekliyor (12:00 fallback)"
        self.stdout.write(
            f"Evening digest slate {slate_date} [{note}]: {len(matches)} matches "
            f"→ {len(recipients)} recipients{' (dry-run)' if dry else ''}"
        )
        self._fan_out(
            recipients, kind, slate_date, dry,
            subject=digest.evening_subject(),
            template="emails/daily_evening.html",
            build_ctx=lambda u: digest.evening_context(
                tournament, slate_date, u,
                matches=matches, leaderboard_base=lb_base, daily_by_user=daily_by_user,
            ),
            text_fn=digest.evening_text,
        )

    # ---- shared fan-out ----

    def _fan_out(self, recipients, kind, slate_date, dry, *, subject, template, build_ctx, text_fn):
        sent = failed = 0
        for user in recipients:
            ctx = build_ctx(user)
            html = render_to_string(template, ctx)  # render even in dry-run to catch errors
            if dry:
                continue
            log = send_logged(
                subject=subject, body=text_fn(ctx), html=html,
                recipient=user.email, kind=kind, user=user, slate_date=slate_date,
            )
            if log.status == EmailLog.FAILED:
                failed += 1
                self.stderr.write(f"  send failed → {user.email}: {log.error}")
            else:
                sent += 1
        if dry:
            self.stdout.write(f"Dry-run OK — rendered {len(recipients)} mails, none sent.")
        else:
            self.stdout.write(f"Done — sent {sent}, failed {failed}.")
