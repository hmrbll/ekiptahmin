"""Repair penalty ActualResults whose 120' score was inflated by the shootout.

Before the map_score fix, football-data's `fullTime` (which folds the shootout
goals into the 120' score) was stored straight into `home_score_aet` /
`away_score_aet`. That inflated score then drove BOTH the displayed result and
the exact/diff/result scoring (via ActualResult.effective_*_score) — e.g. a 1-1
draw won 3-4 on penalties showed and scored as 4-5.

The fix in apps/liveresults/score.py only affects *future* syncs; finalized
matches are never re-fetched, so this one-off command repairs the rows already
written. For each penalty result it recovers the clean 120' score
(`aet - penalties`) — a draw, by definition of going to penalties. Saving the
row re-runs the scoring signals, so ganyan/leaderboard recompute automatically.

Idempotent: a row is only touched when subtracting the penalties yields a
non-negative *draw* that differs from what's stored — once repaired, re-running
is a no-op (a clean 120' draw minus the penalties is negative / not level).

    python manage.py fix_penalty_aet --dry-run   # preview only
    python manage.py fix_penalty_aet             # apply
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.tournament.models import ActualResult


class Command(BaseCommand):
    help = (
        "Strip penalty-shootout goals back out of the stored 120' score "
        "(home/away_score_aet) for penalty matches synced before the map_score "
        "fix. Idempotent. --dry-run to preview."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would change without writing.",
        )

    def handle(self, *args, **opts):
        dry = opts["dry_run"]

        rows = (
            ActualResult.objects
            .filter(went_to_penalties=True)
            .select_related("slot__home_team_actual", "slot__away_team_actual")
            .order_by("slot__scheduled_kickoff")
        )

        fixed = 0
        flagged = 0
        for r in rows:
            if r.home_score_aet is None or r.away_score_aet is None:
                continue
            if r.home_penalties is None or r.away_penalties is None:
                # No shootout score to strip — but a penalty match with an
                # undecided shootout is bad data worth surfacing.
                self._flag(r, "penalty score missing")
                flagged += 1
                continue

            clean_h = r.home_score_aet - r.home_penalties
            clean_a = r.away_score_aet - r.away_penalties

            # The 120' score of a shootout is always a level, non-negative score.
            # If stripping the penalties doesn't yield one, the row is either
            # already clean (re-run) or otherwise inconsistent — leave it.
            inflated = (
                clean_h >= 0 and clean_a >= 0 and clean_h == clean_a
                and (clean_h, clean_a) != (r.home_score_aet, r.away_score_aet)
            )
            if not inflated:
                continue

            self.stdout.write(
                ("  [dry] " if dry else "  ")
                + f"{r.slot.position}: 120' {r.home_score_aet}-{r.away_score_aet} "
                + f"(pen {r.home_penalties}-{r.away_penalties}) → {clean_h}-{clean_a}"
            )
            if not dry:
                r.home_score_aet = clean_h
                r.away_score_aet = clean_a
                # Triggers the scoring signals → ganyan + leaderboard recompute.
                r.save(update_fields=["home_score_aet", "away_score_aet"])
            fixed += 1

            # A repaired row with no penalty winner still can't advance / pay the
            # penalty pool — surface it so the real shootout result gets entered.
            if r.penalty_winner_id is None:
                self._flag(r, "no penalty winner — needs the real shootout result")
                flagged += 1

        style = self.style.HTTP_INFO
        self.stdout.write(style(
            f"{'[dry-run] ' if dry else ''}{fixed} penalty result(s) "
            f"{'would be' if dry else ''} repaired; {flagged} flagged."
        ))

    def _flag(self, r: ActualResult, reason: str) -> None:
        self.stdout.write(self.style.WARNING(f"  ! {r.slot.position}: {reason}"))
