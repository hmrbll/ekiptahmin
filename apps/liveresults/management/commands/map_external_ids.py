"""Map football-data match ids onto our BracketSlots (one-time / re-runnable).

Anchors on the team-code pair + date (see mapping.find_slot_for_match), so it
only maps slots whose teams are known. Group-stage slots map immediately;
knockout slots map once their teams resolve — just re-run after each round.

    python manage.py map_external_ids            # write MatchSync.external_id
    python manage.py map_external_ids --dry-run   # preview only, no writes

Writes only the MatchSync mapping/status — never ActualResult. Safe to re-run.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.liveresults.client import FootballDataClient, FootballDataError
from apps.liveresults.mapping import find_slot_for_match
from apps.liveresults.models import MatchSync
from apps.tournament.models import BracketSlot, Tournament


class Command(BaseCommand):
    help = "Map football-data match ids onto BracketSlots (MatchSync.external_id). Re-runnable."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Show what would be mapped without writing.")
        parser.add_argument("--competition", default=None,
                            help="Override the competition code (default from settings).")

    def handle(self, *args, **opts):
        client = FootballDataClient()
        if not client.is_configured:
            self.stderr.write(self.style.ERROR("FOOTBALL_DATA_API_KEY is not set."))
            return

        tournament = Tournament.objects.filter(is_active=True).first()
        if tournament is None:
            self.stderr.write(self.style.ERROR("No active tournament."))
            return

        try:
            matches = client.get_competition_matches(competition=opts["competition"])
        except FootballDataError as exc:
            self.stderr.write(self.style.ERROR(f"API error: {exc}"))
            return

        slots = list(
            BracketSlot.objects
            .filter(tournament=tournament,
                    home_team_actual__isnull=False, away_team_actual__isnull=False)
            .select_related("home_team_actual", "away_team_actual")
        )

        dry = opts["dry_run"]
        created = updated = skipped = 0
        seen_external: dict[str, str] = {}  # external_id -> slot.position (collision guard)

        for m in matches:
            slot = find_slot_for_match(tournament, m, slots=slots)
            if slot is None:
                continue
            ext = str(m.get("id"))
            status = m.get("status") or ""

            if ext in seen_external and seen_external[ext] != slot.position:
                self.stderr.write(self.style.WARNING(
                    f"Collision: external_id {ext} matches both {seen_external[ext]} and {slot.position}."
                ))
            seen_external[ext] = slot.position

            sync = MatchSync.objects.filter(slot=slot).first()
            if sync is None:
                self.stdout.write(f"  + {slot.position}  ←  {ext}  ({status})")
                if not dry:
                    MatchSync.objects.create(slot=slot, external_id=ext, status=status)
                created += 1
            elif sync.external_id != ext:
                self.stdout.write(f"  ~ {slot.position}  ←  {ext}  (was {sync.external_id or '—'})")
                if not dry:
                    sync.external_id = ext
                    sync.status = status
                    sync.save(update_fields=["external_id", "status"])
                updated += 1
            else:
                skipped += 1

        # Slots still unmapped (teams not resolved yet, or no API match found).
        mapped_slot_ids = {
            s.slot_id for s in MatchSync.objects.filter(slot__tournament=tournament)
            .exclude(external_id="")
        } if not dry else set()
        total_slots = BracketSlot.objects.filter(tournament=tournament).count()

        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO(
            f"{'[dry-run] ' if dry else ''}mapped: +{created} new, ~{updated} changed, "
            f"{skipped} unchanged | API matches: {len(matches)} | slots total: {total_slots}"
        ))
        if not dry and len(mapped_slot_ids) < total_slots:
            self.stdout.write(self.style.WARNING(
                f"{total_slots - len(mapped_slot_ids)} slot(s) still unmapped — likely "
                "knockout slots whose teams aren't resolved yet. Re-run after each round."
            ))
