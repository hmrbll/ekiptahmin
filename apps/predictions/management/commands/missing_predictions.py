"""Report who is missing which predictions in the open rounds.

Complements `revalidate_predictions`: that command finds *stale* rows
(matchup no longer derivable), this one finds *gaps* — editable slots a
user hasn't predicted at all, whether because they never got around to
it or because a revalidation sweep just deleted their stale rows. Run it
before a deadline to know who to nudge.

Only stages currently in a round's editable_stages count: a stage the
admin closed mid-round (e.g. GROUP) can't be filled anymore, so gaps
there aren't actionable.

Usage:
  python manage.py missing_predictions
"""

from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.predictions.models import SlotPrediction
from apps.tournament.models import BracketSlot, PredictionRound


class Command(BaseCommand):
    help = "Report users' missing predictions per open round (editable stages only)."

    def handle(self, *args, **options):
        open_rounds = [r for r in PredictionRound.objects.all() if r.is_open]
        if not open_rounds:
            self.stdout.write("No open rounds — nothing to report.")
            return

        users = list(
            get_user_model().objects.filter(is_active=True).order_by("nickname")
        )
        for pr in open_rounds:
            slots = list(
                BracketSlot.objects
                .filter(tournament=pr.tournament, stage__in=pr.editable_stages.all())
                .select_related("stage")
                .order_by("stage__order", "scheduled_kickoff", "id")
            )
            if not slots:
                continue
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"{pr.name} — {len(slots)} editable slots "
                f"(deadline {pr.deadline:%Y-%m-%d %H:%M} UTC)"
            ))

            predicted_by_user: dict[int, set[int]] = defaultdict(set)
            pairs = SlotPrediction.objects.filter(
                prediction_round=pr, slot__in=slots,
            ).values_list("user_id", "slot_id")
            for user_id, slot_id in pairs:
                predicted_by_user[user_id].add(slot_id)

            for user in users:
                name = user.nickname or user.email
                missing = [s for s in slots if s.id not in predicted_by_user[user.id]]
                if not missing:
                    self.stdout.write(f"  OK   {name}")
                elif len(missing) == len(slots):
                    self.stdout.write(self.style.WARNING(
                        f"  !!   {name}: no predictions at all"
                    ))
                else:
                    positions = ", ".join(s.position for s in missing)
                    self.stdout.write(self.style.WARNING(
                        f"  {len(missing):>2}   {name}: {positions}"
                    ))
