"""Run one live-result sync pass (for local testing / manual ops).

In production the homepage trigger (maybe_sync_live) drives syncing; this
command is the same core, runnable by hand:

    python manage.py sync_live_results --dry-run   # show intended writes only
    python manage.py sync_live_results             # write results + recompute scores

Cheap when nothing is in the live window (no API call, no writes).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.liveresults.sync import sync_live_matches


class Command(BaseCommand):
    help = "Run one live-result sync pass. --dry-run to preview without writing."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Fetch and map, but do not write ActualResult/MatchSync.")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        report = sync_live_matches(dry_run=dry)

        for line in report.lines:
            self.stdout.write(("  [dry] " if dry else "  ") + line)
        for err in report.errors:
            self.stderr.write(self.style.WARNING(f"  ! {err}"))

        style = self.style.WARNING if report.errors else self.style.HTTP_INFO
        self.stdout.write(style(f"{'[dry-run] ' if dry else ''}{report.summary()}"))
