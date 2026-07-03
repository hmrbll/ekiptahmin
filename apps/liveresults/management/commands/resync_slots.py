"""Force re-pull specific slots' results from football-data, bypassing finalize.

The normal sync never re-fetches a finalized match, so a row that finalized
with bad data stays bad: e.g. a shootout captured mid-round (penalties tied,
no winner) or the pre-fix penalty-inflated 120' scores. `fix_penalty_aet`
repairs only the pure-inflation shape; a hybrid row (penalties later corrected
by hand but aet still stale) doesn't subtract back to a draw, so it skips it.

This command is the authoritative repair for all of those: it re-fetches the
named slots' matches from the API and rewrites ActualResult through the exact
same path as the live sync (map_score → update_or_create, source=API), ignoring
both the live window and MatchSync.finalized. Saving re-runs the scoring
signals, so ganyan/leaderboard recompute automatically. Idempotent: an
already-correct row is reported unchanged and not rewritten.

Manually entered results (source=MANUAL) are authoritative — the wizard is the
final word — so they are skipped unless --force is given.

    python manage.py resync_slots R32-3 --dry-run   # preview only
    python manage.py resync_slots R32-3 R32-2       # apply
    python manage.py resync_slots R32-3 --force     # also overwrite MANUAL rows
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.liveresults.client import FootballDataClient, FootballDataError
from apps.liveresults.models import MatchSync
from apps.liveresults.score import ScoreMappingError, map_score
from apps.liveresults.sync import _penalty_winner_team, _result_changed, _RESULT_FIELDS
from apps.tournament.models import ActualResult, BracketSlot, Tournament


class Command(BaseCommand):
    help = (
        "Re-fetch the given slots' results from football-data and rewrite "
        "ActualResult through the normal sync path, bypassing the live window "
        "and MatchSync.finalized. For stuck-finalized rows (mid-shootout "
        "captures, stale scores). Idempotent. --dry-run to preview."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "positions", nargs="+", metavar="position",
            help="Slot positions to resync, e.g. R32-3 R16-1.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would change without writing.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Also overwrite manually entered results (source=MANUAL), "
                 "which are otherwise authoritative and skipped.",
        )

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        now = timezone.now()

        tournament = Tournament.objects.filter(is_active=True).first()
        if tournament is None:
            self.stderr.write(self.style.ERROR("No active tournament."))
            return

        slots = []
        for pos in opts["positions"]:
            slot = (
                BracketSlot.objects
                .filter(tournament=tournament, position=pos)
                .select_related("live_sync", "home_team_actual", "away_team_actual")
                .first()
            )
            if slot is None:
                self.stderr.write(self.style.ERROR(f"{pos}: no such slot."))
                continue
            msync = getattr(slot, "live_sync", None)
            if msync is None or not msync.external_id:
                self.stderr.write(self.style.ERROR(
                    f"{pos}: not mapped to a football-data match — run map_external_ids first."
                ))
                continue
            slots.append(slot)
        if not slots:
            return

        client = FootballDataClient()
        if not client.is_configured:
            self.stderr.write(self.style.ERROR("FOOTBALL_DATA_API_KEY not set."))
            return
        try:
            # One unfiltered fetch covers any set of slots (the whole WC is one
            # competition; football-data caps *windowed* queries, not this).
            by_id = {str(m.get("id")): m for m in client.get_competition_matches()}
        except FootballDataError as exc:
            self.stderr.write(self.style.ERROR(f"API error: {exc}"))
            return

        for slot in slots:
            msync: MatchSync = slot.live_sync
            payload = by_id.get(msync.external_id)
            if payload is None:
                self.stderr.write(self.style.ERROR(
                    f"{slot.position}: external id {msync.external_id} not in the API response."
                ))
                continue

            status = payload.get("status") or ""
            try:
                fields = map_score(payload.get("score") or {})
            except ScoreMappingError as exc:
                self.stderr.write(self.style.ERROR(f"{slot.position}: {exc}"))
                continue
            if fields is None:
                self.stdout.write(f"{slot.position}: {status} — no usable score yet, skipped.")
                continue
            if not (slot.home_team_actual_id and slot.away_team_actual_id):
                self.stderr.write(self.style.ERROR(
                    f"{slot.position}: slot teams unresolved — resolve the bracket first."
                ))
                continue

            pw_team = _penalty_winner_team(slot, fields["penalty_winner_side"])
            existing = ActualResult.objects.filter(slot=slot).first()
            if (
                existing is not None
                and existing.source == ActualResult.SOURCE_MANUAL
                and not opts["force"]
            ):
                self.stdout.write(
                    f"{slot.position}: manual result — skipped (--force to overwrite)."
                )
                continue
            changed = _result_changed(existing, fields, pw_team)

            scoreline = f"{fields['home_score']}-{fields['away_score']}"
            if fields["went_to_extra_time"]:
                scoreline += f" (aet {fields['home_score_aet']}-{fields['away_score_aet']}"
                if fields["went_to_penalties"]:
                    scoreline += (
                        f", pen {fields['home_penalties']}-{fields['away_penalties']}"
                        f", winner {pw_team.code if pw_team else '?'}"
                    )
                scoreline += ")"

            if not changed:
                self.stdout.write(f"{slot.position}: {status} {scoreline} [unchanged]")
                continue

            if existing is not None:
                self.stdout.write(
                    f"{slot.position}: before 90' {existing.home_score}-{existing.away_score}"
                    f" aet {existing.home_score_aet}-{existing.away_score_aet}"
                    f" pen {existing.home_penalties}-{existing.away_penalties}"
                    f" winner {existing.penalty_winner.code if existing.penalty_winner else '--'}"
                )
            self.stdout.write(
                ("  [dry] " if dry else "  ")
                + f"{slot.position}: {'would write' if dry else 'wrote'} {status} {scoreline}"
            )
            if dry:
                continue

            ActualResult.objects.update_or_create(
                slot=slot,
                defaults={
                    **{f: fields[f] for f in _RESULT_FIELDS},
                    "penalty_winner": pw_team,
                    "source": ActualResult.SOURCE_API,
                },
            )
            msync.status = status
            msync.finalized = status == MatchSync.STATUS_FINISHED
            msync.last_synced_at = now
            msync.save(update_fields=["status", "finalized", "last_synced_at"])

        self.stdout.write(self.style.HTTP_INFO(
            f"{'[dry-run] ' if dry else ''}done — scoring recomputes via the save signal."
        ))
