"""Cascade derivation + downstream invalidation for bracket predictions.

A knockout slot's teams are derived from the user's earlier predictions
(source-slot winner/loser, group standings, best-third allocation). When the
user edits an upstream prediction, the derived matchup of downstream slots can
change — any stored prediction whose teams no longer match the derivation is
stale and must be deleted ("looks never predicted"), per product rule.

Round isolation: derivation reads ONLY the predictions made in the round being
derived — a later round never inherits an earlier round's bracket. Each round is
predicted from scratch (e.g. Son 16 in "Grup sonrası" derives from that round's
own Son 32 picks, not the pre-tournament ones). The shared cross-round
foundation is *actual* results: as games are played, `tournament.resolver` writes
the real teams into `BracketSlot.*_team_actual`, which every derivation path
checks first — so resolved matchups show in every round.

Two layers:
- `derive_cascaded_team` / `resolve_slot_side_team`: shared derivation used by
  both the form (rendering/locking team fields) and the invalidation pass.
- `invalidate_stale_predictions`: post-save sweep over the user's knockout
  predictions in one round, deleting rows whose matchup went stale. Runs in
  stage order so a deletion in R16 cascades naturally into QF/SF/Final on the
  same pass (their source derivation comes back None or different).

Scope: only the round being edited. Predictions in closed rounds are scored
history and must never be touched here.
"""

from django.db.models import Q

from apps.tournament.models import BracketSlot, Team

from .models import SlotPrediction
from .standings import (
    best_third_from_qualifying,
    qualifying_thirds,
    standings_for_group,
)


def derive_cascaded_team(user, source_slot: BracketSlot, source_kind: str, prediction_round):
    """Look up the user's prediction for `source_slot` in `prediction_round` and
    return the winner or loser team based on `source_kind`. Returns None if the
    user has no prediction for that slot in this round (round-isolated — an
    earlier round's pick is never inherited) or the prediction has no determinate
    winner (draw without penalty winner).
    """
    if source_slot is None:
        return None
    pred = (
        SlotPrediction.objects
        .filter(user=user, slot=source_slot, prediction_round=prediction_round)
        .select_related("home_team", "away_team", "penalty_winner")
        .first()
    )
    if pred is None:
        return None
    return pred.winner_team() if source_kind == BracketSlot.SOURCE_KIND_WINNER else pred.loser_team()


def resolve_slot_side_team(user, slot: BracketSlot, side: str, prediction_round, memo: dict | None = None):
    """Best-effort: figure out which Team belongs in `side` of `slot` for this user.

    Precedence: actual (admin-set / resolver) → upstream slot cascade → group
    standings → best-third. The prediction-derived paths read only
    `prediction_round` (round isolation); `*_team_actual` is the shared
    cross-round foundation and wins outright. Returns the Team or None if no
    path resolves (e.g. user hasn't predicted enough upstream slots in this
    round yet).

    `memo` (optional, shared across calls within one pass) caches group
    standings and the qualifying-thirds computation — those depend only on
    group predictions, which an invalidation pass never modifies. Source-slot
    lookups are deliberately NOT memoized: the pass deletes knockout
    predictions as it goes and later derivations must see those deletions.
    """
    if memo is None:
        memo = {}

    actual = getattr(slot, f"{side}_team_actual")
    if actual:
        return actual

    source_slot = getattr(slot, f"{side}_source_slot")
    if source_slot:
        kind = getattr(slot, f"{side}_source_kind")
        return derive_cascaded_team(user, source_slot, kind, prediction_round)

    group_letter = getattr(slot, f"{side}_source_group_letter")
    group_position = getattr(slot, f"{side}_source_group_position")
    if group_letter and group_position:
        return _group_team_memo(user, slot.tournament, group_letter, group_position, prediction_round, memo)

    thirds_groups = getattr(slot, f"{side}_source_thirds_groups")
    if thirds_groups:
        if "thirds" not in memo:
            memo["thirds"] = qualifying_thirds(user, slot.tournament, prediction_round)
        qualifying, third_by_letter = memo["thirds"]
        return best_third_from_qualifying(slot.tournament, slot, qualifying, third_by_letter)

    return None


def _group_team_memo(user, tournament, letter: str, position: int, prediction_round, memo: dict):
    """`derive_group_team` with the standings table cached in `memo`."""
    key = ("group_standings", letter)
    if key not in memo:
        memo[key] = standings_for_group(
            user, tournament, letter, prediction_round, pad_with_zeros=False,
        )
    standings = memo[key]
    if len(standings) < position:
        return None
    target_code = standings[position - 1].team_code
    return Team.objects.filter(tournament=tournament, code=target_code).first()


def _side_has_source(slot: BracketSlot, side: str) -> bool:
    """True when `side` of `slot` is determined by some source (actual teams,
    upstream slot, group standings, thirds) rather than a free user pick."""
    return bool(
        getattr(slot, f"{side}_team_actual_id")
        or getattr(slot, f"{side}_source_slot_id")
        or (
            getattr(slot, f"{side}_source_group_letter")
            and getattr(slot, f"{side}_source_group_position")
        )
        or getattr(slot, f"{side}_source_thirds_groups")
    )


def invalidate_stale_predictions(user, prediction_round) -> list[BracketSlot]:
    """Delete the user's knockout predictions in `prediction_round` whose
    stored matchup no longer matches what their current upstream predictions
    derive. Returns the slots whose predictions were deleted.

    Called after every successful prediction save. Walks stages in ascending
    order so each deletion is visible to the derivations of later stages
    (deleting a stale R16 row makes the dependent QF derivation fail → that
    row is deleted too, and so on down the bracket).

    A side with no source at all (free pick) is never grounds for deletion.
    A sourced side that derives to None (upstream prediction missing/deleted)
    counts as stale — the form would block editing it anyway.

    Deletes via instance.delete() so scoring-cache signals fire per row.
    """
    memo: dict = {}
    deleted_slots: list[BracketSlot] = []
    preds = list(
        SlotPrediction.objects
        .filter(user=user, prediction_round=prediction_round)
        .exclude(slot__stage__kind="GROUP")
        .select_related(
            "slot__stage", "slot__tournament",
            "slot__home_team_actual", "slot__away_team_actual",
            "slot__home_source_slot", "slot__away_source_slot",
        )
        .order_by("slot__stage__order", "slot__scheduled_kickoff", "slot_id")
    )
    for pred in preds:
        if _is_stale(user, pred, memo):
            pred.delete()
            deleted_slots.append(pred.slot)
    return deleted_slots


def _is_stale(user, pred: SlotPrediction, memo: dict) -> bool:
    for side in ("home", "away"):
        slot = pred.slot
        if not _side_has_source(slot, side):
            continue
        derived = resolve_slot_side_team(user, slot, side, pred.prediction_round, memo=memo)
        if derived is None or derived.id != getattr(pred, f"{side}_team_id"):
            return True
    return False


def downstream_slots(slot: BracketSlot) -> list[BracketSlot]:
    """All slots transitively fed by `slot` through home/away source-slot
    links, in stage order. Used to refresh dependent rows in the UI after a
    knockout prediction save (their displayed teams may have changed even when
    no stored prediction was deleted).
    """
    collected: dict[int, BracketSlot] = {}
    frontier = [slot.id]
    while frontier:
        children = list(
            BracketSlot.objects
            .filter(Q(home_source_slot_id__in=frontier) | Q(away_source_slot_id__in=frontier))
            .select_related("stage", "home_source_slot", "away_source_slot",
                            "home_team_actual", "away_team_actual")
        )
        frontier = [c.id for c in children if c.id not in collected]
        for child in children:
            collected.setdefault(child.id, child)
    return sorted(collected.values(), key=lambda s: (s.stage.order, s.scheduled_kickoff, s.id))
