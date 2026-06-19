"""Read-only connectivity probe for football-data.org.

Drop FOOTBALL_DATA_API_KEY into your .env, then run this to verify, WITHOUT
touching the database:
- auth works and the World Cup competition code is right,
- finished matches come back with usable scores,
- our team-code mapping lines up with the provider's `tla`.

Examples:
    python manage.py fd_probe --finished
    python manage.py fd_probe --status IN_PLAY,PAUSED
    python manage.py fd_probe --date-from 2026-06-11 --date-to 2026-06-19
    python manage.py fd_probe --finished --raw-score   # dump full score JSON

This is a diagnostic, not part of the live path — it makes one API call and
prints. Nothing is written.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.liveresults.client import FootballDataClient, FootballDataError
from apps.liveresults.mapping import find_slot_for_match
from apps.tournament.models import Tournament


class Command(BaseCommand):
    help = "Read-only probe of football-data.org (no DB writes). See module docstring."

    def add_arguments(self, parser):
        parser.add_argument("--finished", action="store_true",
                            help="Only FINISHED matches (shorthand for --status FINISHED).")
        parser.add_argument("--status", default=None,
                            help="Comma-separated status filter, e.g. 'IN_PLAY,PAUSED'.")
        parser.add_argument("--date-from", dest="date_from", default=None, help="YYYY-MM-DD.")
        parser.add_argument("--date-to", dest="date_to", default=None, help="YYYY-MM-DD.")
        parser.add_argument("--competition", default=None,
                            help="Override the competition code (default from settings, 'WC').")
        parser.add_argument("--raw-score", action="store_true",
                            help="Print the full raw score object for each match (schema calibration).")

    def handle(self, *args, **opts):
        client = FootballDataClient()
        if not client.is_configured:
            self.stderr.write(self.style.ERROR(
                "FOOTBALL_DATA_API_KEY is not set. Add it to your .env and retry."
            ))
            return

        status = opts["status"]
        if opts["finished"]:
            status = "FINISHED"

        self.stdout.write(self.style.HTTP_INFO(
            f"Fetching competition={opts['competition'] or 'WC (settings)'} "
            f"status={status or 'ANY'} "
            f"window={opts['date_from'] or '-'}..{opts['date_to'] or '-'}"
        ))

        try:
            matches = client.get_competition_matches(
                competition=opts["competition"],
                date_from=opts["date_from"],
                date_to=opts["date_to"],
                status=status,
            )
        except FootballDataError as exc:
            self.stderr.write(self.style.ERROR(f"API error: {exc}"))
            return

        if not matches:
            self.stdout.write(self.style.WARNING("No matches returned for that filter."))
            return

        tournament = Tournament.objects.filter(is_active=True).first()
        if tournament is None:
            self.stdout.write(self.style.WARNING(
                "No active tournament — showing API data without slot mapping."
            ))

        slots = None  # find_slot_for_match will query lazily/per-call

        matched = unmatched = 0
        for m in matches:
            home = (m.get("homeTeam") or {})
            away = (m.get("awayTeam") or {})
            score = m.get("score") or {}
            full = score.get("fullTime") or {}

            slot = None
            if tournament is not None:
                slot = find_slot_for_match(tournament, m, slots=slots)
            if slot is not None:
                matched += 1
                map_str = self.style.SUCCESS(f"→ {slot.position}")
            else:
                unmatched += 1
                map_str = self.style.WARNING("→ NO MATCH")

            self.stdout.write(
                f"[{m.get('id')}] {m.get('utcDate')} {m.get('status')}"
                + (f" {m.get('minute')}'" if m.get("minute") else "")
                + f"  {home.get('tla') or '?'} {full.get('home')}-{full.get('away')} {away.get('tla') or '?'}"
                + f"  (dur={score.get('duration')}, win={score.get('winner')})  {map_str}"
            )
            if opts["raw_score"]:
                self.stdout.write("    score=" + json.dumps(score, ensure_ascii=False))

        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO(
            f"Total {len(matches)} | mapped {matched} | unmapped {unmatched}"
        ))
        if unmatched:
            self.stdout.write(self.style.WARNING(
                "Unmapped rows usually mean team-code (tla) divergence or unresolved "
                "knockout slots. Calibrate via TLA_OVERRIDES in apps/liveresults/mapping.py."
            ))
