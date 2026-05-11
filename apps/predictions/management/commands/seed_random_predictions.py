"""Fill one user's bracket with random predictions for one prediction round.

Used to seed dev / prod with realistic-looking sample data so the leaderboard
and results pages have someone besides the primary user to render.

Iterates slots in stage progression order (GROUP → R32 → R16 → QF → SF →
THIRD → FINAL) so each knockout slot's team cascade can resolve from the
predictions written earlier in this same run.

Usage:
  python manage.py seed_random_predictions --user-email test@example.com
  python manage.py seed_random_predictions --user-email test@example.com --seed 42 --overwrite
"""

import random

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.predictions.forms import _resolve_slot_side_team
from apps.predictions.models import SlotPrediction
from apps.tournament.models import BracketSlot, PredictionRound, Tournament


def _random_regulation_score() -> int:
    """0-3 with a slight bias toward 1-2 (typical football scoreline)."""
    return random.choices([0, 1, 2, 3], weights=[2, 4, 3, 1])[0]


def _random_penalty_pair() -> tuple[int, int]:
    """Distinct integers in [3, 5] — first/second team's penalty count."""
    a = random.randint(3, 5)
    b = random.randint(3, 5)
    while a == b:
        b = random.randint(3, 5)
    return a, b


class Command(BaseCommand):
    help = "Fill one user's bracket with random predictions for one prediction round."

    def add_arguments(self, parser):
        parser.add_argument("--user-email", required=True)
        parser.add_argument(
            "--round-order", type=int, default=0,
            help="Prediction round order (default 0 = Pre-turnuva).",
        )
        parser.add_argument("--tournament-slug", default=None)
        parser.add_argument(
            "--seed", type=int, default=None,
            help="RNG seed — pass the same value on dev and prod for matching output.",
        )
        parser.add_argument(
            "--overwrite", action="store_true",
            help="Replace existing predictions for this (user, round). Default: skip.",
        )

    def handle(self, *args, **options):
        if options.get("seed") is not None:
            random.seed(options["seed"])

        User = get_user_model()
        user = User.objects.filter(email=options["user_email"]).first()
        if user is None:
            self.stdout.write(self.style.ERROR(
                f"User not found: {options['user_email']}"
            ))
            return

        slug = options.get("tournament_slug")
        tournament = (
            Tournament.objects.get(slug=slug) if slug
            else Tournament.objects.filter(is_active=True).first()
        )
        if tournament is None:
            self.stdout.write(self.style.ERROR("No active tournament."))
            return

        pr = PredictionRound.objects.filter(
            tournament=tournament, order=options["round_order"],
        ).first()
        if pr is None:
            self.stdout.write(self.style.ERROR(
                f"PredictionRound order={options['round_order']} not found."
            ))
            return

        slots = list(
            BracketSlot.objects
            .filter(tournament=tournament, stage__in=pr.editable_stages.all())
            .select_related("stage", "home_team_actual", "away_team_actual",
                            "home_source_slot", "away_source_slot")
            .order_by("stage__order", "scheduled_kickoff")
        )

        created = updated = skipped = 0
        with transaction.atomic():
            for slot in slots:
                # Resolve teams. Group slots have fixed teams; knockout slots
                # cascade from predictions written earlier in this same loop.
                if slot.home_team_actual_id and slot.away_team_actual_id:
                    home = slot.home_team_actual
                    away = slot.away_team_actual
                else:
                    home = _resolve_slot_side_team(user, slot, "home")
                    away = _resolve_slot_side_team(user, slot, "away")

                if home is None or away is None:
                    self.stdout.write(self.style.WARNING(
                        f"  Skipping {slot.position}: could not resolve teams."
                    ))
                    skipped += 1
                    continue

                existing = SlotPrediction.objects.filter(
                    user=user, prediction_round=pr, slot=slot,
                ).first()
                if existing and not options["overwrite"]:
                    skipped += 1
                    continue

                home_score = _random_regulation_score()
                away_score = _random_regulation_score()

                penalty_winner = None
                home_pen = away_pen = None
                is_knockout = slot.stage.kind != "GROUP"
                if is_knockout and home_score == away_score:
                    home_pen, away_pen = _random_penalty_pair()
                    penalty_winner = home if home_pen > away_pen else away

                SlotPrediction.objects.update_or_create(
                    user=user, prediction_round=pr, slot=slot,
                    defaults={
                        "home_team": home,
                        "away_team": away,
                        "home_score": home_score,
                        "away_score": away_score,
                        "penalty_winner": penalty_winner,
                        "home_penalties": home_pen,
                        "away_penalties": away_pen,
                    },
                )
                if existing:
                    updated += 1
                else:
                    created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. created: {created} · updated: {updated} · skipped: {skipped}"
        ))
