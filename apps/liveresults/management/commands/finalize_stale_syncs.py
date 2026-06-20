"""Finalize MatchSync rows for matches that are over but were never marked done.

A live sync normally flips `MatchSync.finalized` (and sets status=FINISHED) the
moment the provider reports FINISHED. When that signal never arrives — an API
gap, or a result entered by hand — the row stays IN_PLAY/finalized=False forever.
Past its live cap the poller stops requesting it, so it never self-heals, and the
match falls into the gap between the live module (drops it after the cap) and
"Son sonuçlar" (still treats it as live). See docs/live-results.md.

This command enforces the invariant "a match with a final result that is past its
live window is FINISHED/finalized". It's idempotent — re-running it is a no-op
once everything is reconciled.

    python manage.py finalize_stale_syncs --dry-run   # preview only
    python manage.py finalize_stale_syncs             # apply
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.liveresults.models import MatchSync
from apps.liveresults.sync import live_cap
from apps.tournament.models import ActualResult


class Command(BaseCommand):
    help = (
        "Finalize MatchSync rows for matches that are over (final result + past "
        "live cap, or already FINISHED) but were never marked finalized. "
        "--dry-run to preview."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would change without writing.",
        )

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        now = timezone.now()

        scored_slot_ids = set(ActualResult.objects.values_list("slot_id", flat=True))
        rows = (
            MatchSync.objects
            .filter(finalized=False)
            .select_related("slot__stage")
            .order_by("slot__scheduled_kickoff")
        )

        fixed = 0
        for ms in rows:
            # Only matches we actually have a final score for are "over".
            if ms.slot_id not in scored_slot_ids:
                continue
            past_cap = now > ms.slot.scheduled_kickoff + live_cap(ms.slot.stage.kind)
            is_finished = ms.status == MatchSync.STATUS_FINISHED
            # Never touch a match still within its live cap — it may genuinely be
            # in play (the ActualResult is then just the live running score).
            if not (is_finished or past_cap):
                continue

            self.stdout.write(
                ("  [dry] " if dry else "  ")
                + f"{ms.slot.position}: {ms.status or '—'}/"
                + ("final" if ms.finalized else "open")
                + " → FINISHED/final"
            )
            if not dry:
                ms.status = MatchSync.STATUS_FINISHED
                ms.finalized = True
                ms.save(update_fields=["status", "finalized"])
            fixed += 1

        style = self.style.HTTP_INFO
        self.stdout.write(style(
            f"{'[dry-run] ' if dry else ''}{fixed} match sync row(s) "
            f"{'would be' if dry else ''} finalized."
        ))
