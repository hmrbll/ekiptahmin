"""Import SlotPrediction rows from a JSON file produced by `export_predictions`.

FK resolution by natural identifiers (email / position / order / code). Idempotent
— a (user, round, slot) row that already exists is updated in place rather than
duplicated. Rows are skipped (with a warning) when any FK can't be resolved on
the target DB.

Usage:
  python manage.py import_predictions data/predictions_snapshot.json
"""

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.predictions.models import SlotPrediction
from apps.tournament.models import BracketSlot, PredictionRound, Team, Tournament


class Command(BaseCommand):
    help = "Import SlotPrediction rows from an export_predictions JSON snapshot."

    def add_arguments(self, parser):
        parser.add_argument("path", help="JSON file produced by export_predictions.")
        parser.add_argument(
            "--tournament-slug", default=None,
            help="Tournament slug (defaults to the is_active=True tournament).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Validate FK resolution and report counts without writing rows.",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            self.stdout.write(self.style.ERROR(f"File not found: {path}"))
            return

        records = json.loads(path.read_text(encoding="utf-8"))
        slug = options.get("tournament_slug")
        if slug:
            tournament = Tournament.objects.get(slug=slug)
        else:
            tournament = Tournament.objects.filter(is_active=True).first()
            if tournament is None:
                self.stdout.write(self.style.ERROR("No active tournament found."))
                return

        User = get_user_model()

        # Cache lookups so we don't query per-row.
        users_by_email = {u.email: u for u in User.objects.all()}
        rounds_by_order = {
            r.order: r for r in PredictionRound.objects.filter(tournament=tournament)
        }
        slots_by_position = {
            s.position: s for s in BracketSlot.objects.filter(tournament=tournament)
        }
        teams_by_code = {
            t.code: t for t in Team.objects.filter(tournament=tournament)
        }

        created = updated = skipped = 0
        with transaction.atomic():
            for rec in records:
                user = users_by_email.get(rec["user_email"])
                pr = rounds_by_order.get(rec["round_order"])
                slot = slots_by_position.get(rec["slot_position"])
                home = teams_by_code.get(rec["home_team_code"])
                away = teams_by_code.get(rec["away_team_code"])
                pen_winner = (
                    teams_by_code.get(rec["penalty_winner_code"])
                    if rec.get("penalty_winner_code") else None
                )

                missing = [
                    name for name, v in [
                        ("user", user), ("round", pr), ("slot", slot),
                        ("home_team", home), ("away_team", away),
                    ] if v is None
                ]
                if missing:
                    self.stdout.write(self.style.WARNING(
                        f"  Skipping {rec['slot_position']} / "
                        f"{rec['user_email']} — missing FK(s): {', '.join(missing)}"
                    ))
                    skipped += 1
                    continue

                if options["dry_run"]:
                    continue

                _, was_created = SlotPrediction.objects.update_or_create(
                    user=user, prediction_round=pr, slot=slot,
                    defaults={
                        "home_team": home,
                        "away_team": away,
                        "home_score": rec["home_score"],
                        "away_score": rec["away_score"],
                        "penalty_winner": pen_winner,
                        "home_penalties": rec.get("home_penalties"),
                        "away_penalties": rec.get("away_penalties"),
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        verb = "DRY-RUN" if options["dry_run"] else "Imported"
        self.stdout.write(self.style.SUCCESS(
            f"{verb}. Total: {len(records)} · created: {created} · updated: {updated} · skipped: {skipped}"
        ))
