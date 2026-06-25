"""Django ORM → ganyan engine bridge.

Converts BracketSlot + SlotPrediction + ActualResult rows into the engine's
dataclasses, runs `compute_slot`, and returns the per-user UserScore plus
per-criterion PoolStats. The ORM upsert lives in `ganyan_cache.py`.
"""

from decimal import Decimal
from typing import Optional


from apps.predictions.models import SlotPrediction
from apps.scoring import ganyan
from apps.tournament.models import ActualResult, BracketSlot


def _build_prediction(pred: SlotPrediction) -> ganyan.Prediction:
    return ganyan.Prediction(
        round_order=pred.prediction_round.order,
        round_weight=pred.prediction_round.weight,
        home_team=pred.home_team.code,
        away_team=pred.away_team.code,
        home_score=pred.home_score,
        away_score=pred.away_score,
        penalty_winner=pred.penalty_winner.code if pred.penalty_winner_id else None,
        home_penalties=pred.home_penalties,
        away_penalties=pred.away_penalties,
    )


def _build_result(actual: ActualResult) -> ganyan.Result:
    # exact/diff/result judge the 120' score for ET knockout matches, else the
    # 90' score (see ActualResult.effective_*_score). Penalty criteria are
    # separate and unaffected (a penalty match is a draw at both 90' and 120').
    return ganyan.Result(
        home_team=actual.slot.home_team_actual.code if actual.slot.home_team_actual_id else "",
        away_team=actual.slot.away_team_actual.code if actual.slot.away_team_actual_id else "",
        home_score=actual.effective_home_score,
        away_score=actual.effective_away_score,
        went_to_penalties=actual.went_to_penalties,
        penalty_winner=actual.penalty_winner.code if actual.penalty_winner_id else None,
        home_penalties=actual.home_penalties,
        away_penalties=actual.away_penalties,
    )


def _stage_pools(slot: BracketSlot) -> ganyan.StagePools:
    s = slot.stage
    return ganyan.StagePools(
        pool_exact=s.pool_exact,
        pool_diff=s.pool_diff,
        pool_result=s.pool_result,
        pool_penalty_winner=s.pool_penalty_winner,
        pool_penalty_score=s.pool_penalty_score,
        pool_penalty_diff=s.pool_penalty_diff,
    )


def _gather_predictions_by_user(slot: BracketSlot) -> dict[int, list[ganyan.Prediction]]:
    preds = (
        SlotPrediction.objects
        .filter(slot=slot)
        .select_related("home_team", "away_team", "penalty_winner", "prediction_round")
    )
    by_user: dict[int, list[ganyan.Prediction]] = {}
    for p in preds:
        by_user.setdefault(p.user_id, []).append(_build_prediction(p))
    return by_user


def compute_slot_scores(slot: BracketSlot) -> Optional[tuple[
    dict[int, ganyan.UserScore], list[ganyan.PoolStats]
]]:
    """Run the ganyan engine for one slot.

    Returns None if there is no actual result yet — caller treats this as
    "no_result" (clears any old GanyanScore rows for this slot but writes
    pre-result MatchPool rows via compute_pre_result_pools).
    Returns ({}, [zero-stats]) if the slot has a result but nobody predicted.
    """
    actual = (
        ActualResult.objects
        .filter(slot=slot)
        .select_related(
            "slot__home_team_actual", "slot__away_team_actual", "penalty_winner",
        )
        .first()
    )
    if actual is None:
        return None

    predictions_by_user = _gather_predictions_by_user(slot)
    pools = _stage_pools(slot)
    result = _build_result(actual)

    user_scores, pool_stats = ganyan.compute_slot(predictions_by_user, result, pools)
    return user_scores, pool_stats


def compute_pre_result_pools(slot: BracketSlot) -> list[ganyan.PoolStats]:
    """Compute pre-result MatchPool stats from current predictions.

    Used when a slot has predictions but no ActualResult yet — populates
    MatchPool.breakdown so the ganyan tablosu UI can show counts.
    """
    predictions_by_user = _gather_predictions_by_user(slot)
    pools = _stage_pools(slot)
    return ganyan.compute_pre_result_pools(predictions_by_user, pools)


def potential_max_scores_for_slot(
    slot: BracketSlot, predictions: list[SlotPrediction],
) -> dict[int, Decimal]:
    """Best-case ganyan payout per user for an as-yet-unscored slot.

    `predictions` is one SlotPrediction per user (the latest shown on the
    all-predictions page). Returns {user_id: Decimal} only for predictions whose
    matchup lines up with the slot's actual fixture; unreachable (wrong-matchup)
    picks are omitted. Empty when the slot's teams aren't both set yet.
    """
    if not (slot.home_team_actual_id and slot.away_team_actual_id):
        return {}
    pools = _stage_pools(slot)
    pred_by_user = {p.user_id: _build_prediction(p) for p in predictions}
    return ganyan.potential_max_scores(
        pred_by_user, pools,
        slot.home_team_actual.code, slot.away_team_actual.code,
    )


def potential_max_scores_for_slot_multi(
    slot: BracketSlot, predictions_by_user: dict[int, list[SlotPrediction]],
) -> dict[int, list[Decimal]]:
    """Per-prediction best-case payout for the all-predictions card.

    `predictions_by_user` maps a user id to that user's picks on this slot — one
    per round, already filtered to the slot's actual fixture and ordered by
    round. Returns ``{user_id: [Decimal aligned to each list]}`` so the caller
    can drop one "en fazla" onto every row. Empty when the slot's teams aren't
    both set yet.
    """
    if not (slot.home_team_actual_id and slot.away_team_actual_id):
        return {}
    pools = _stage_pools(slot)
    pred_objs = {
        uid: [_build_prediction(p) for p in preds]
        for uid, preds in predictions_by_user.items()
    }
    return ganyan.potential_max_scores_multi(
        pred_objs, pools,
        slot.home_team_actual.code, slot.away_team_actual.code,
    )
