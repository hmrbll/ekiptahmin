"""SlotScore upsert helpers driven by signals + management commands.

`recompute_slot_for_user(user, slot)` is the single point that calls the
engine bridge and writes one row. Bulk helpers loop over it.
"""

from decimal import Decimal

from apps.predictions.models import SlotPrediction
from apps.tournament.models import BracketSlot

from .django_bridge import score_slot_for_user
from .models import SlotScore


def recompute_slot_for_user(user, slot: BracketSlot) -> SlotScore:
    """Upsert one SlotScore row from the engine's current verdict.

    If no ActualResult exists yet, the row is stored with zero points and
    `matchup_type = NO_RESULT` — keeps leaderboard joins clean.
    """
    breakdown = score_slot_for_user(user, slot)
    if breakdown is None:
        defaults = {
            "points_match": Decimal("0"),
            "points_penalty": Decimal("0"),
            "total": Decimal("0"),
            "matchup_type": SlotScore.NO_RESULT,
            "earning_round_order": None,
        }
    else:
        defaults = {
            "points_match": breakdown.points_match,
            "points_penalty": breakdown.points_penalty,
            "total": breakdown.total,
            "matchup_type": breakdown.matchup_type,
            "earning_round_order": breakdown.earning_round_order,
        }
    score, _ = SlotScore.objects.update_or_create(
        user=user, slot=slot, defaults=defaults,
    )
    return score


def recompute_slot_for_all_users(slot: BracketSlot) -> int:
    """Recompute every user who has predicted `slot`. Returns row count.

    Triggered when a slot's ActualResult is created/updated/deleted.
    """
    user_ids = (
        SlotPrediction.objects
        .filter(slot=slot)
        .values_list("user_id", flat=True)
        .distinct()
    )
    from django.contrib.auth import get_user_model
    User = get_user_model()
    users = User.objects.filter(id__in=list(user_ids))
    n = 0
    for user in users:
        recompute_slot_for_user(user, slot)
        n += 1
    return n


def recompute_user_all_slots(user, tournament) -> int:
    """Recompute every slot in `tournament` for `user`. For admin backfills."""
    slots = BracketSlot.objects.filter(tournament=tournament)
    n = 0
    for slot in slots:
        recompute_slot_for_user(user, slot)
        n += 1
    return n
