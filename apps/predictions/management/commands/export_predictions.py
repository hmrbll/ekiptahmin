"""Export SlotPrediction rows to a JSON file using natural identifiers.

FKs are resolved by user.email, slot.position, prediction_round.order, and
team.code — so the same JSON can be loaded on a different DB (e.g. prod)
where ID space is independent.

Usage:
  python manage.py export_predictions --output data/predictions_snapshot.json
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.predictions.models import SlotPrediction


def _serialize(pred: SlotPrediction) -> dict:
    return {
        "user_email": pred.user.email,
        "round_order": pred.prediction_round.order,
        "slot_position": pred.slot.position,
        "home_team_code": pred.home_team.code,
        "away_team_code": pred.away_team.code,
        "home_score": pred.home_score,
        "away_score": pred.away_score,
        "penalty_winner_code": pred.penalty_winner.code if pred.penalty_winner_id else None,
        "home_penalties": pred.home_penalties,
        "away_penalties": pred.away_penalties,
    }


class Command(BaseCommand):
    help = "Export SlotPrediction rows to a JSON file with natural identifiers."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True, help="Path to write JSON.")
        parser.add_argument(
            "--user-email", default=None,
            help="Optional: restrict export to one user's predictions.",
        )

    def handle(self, *args, **options):
        qs = SlotPrediction.objects.select_related(
            "user", "prediction_round", "slot", "home_team", "away_team", "penalty_winner",
        )
        if options.get("user_email"):
            qs = qs.filter(user__email=options["user_email"])

        records = [_serialize(p) for p in qs]
        out_path = Path(options["output"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(
            f"Exported {len(records)} prediction(s) to {out_path}"
        ))
