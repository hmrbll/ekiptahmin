"""Django ORM → pure-Python scoring engine bridge.

The engine in `apps/scoring/engine.py` knows nothing about Django. This module
converts SlotPrediction / ActualResult / Stage / PredictionRound rows into
the engine's plain dataclasses, calls `score_slot`, and returns the breakdown.

Used by:
- `apps.scoring.cache.recompute_*` to write SlotScore rows
- `manage.py recompute_scores` for backfills

Stage scoring config is fetched lazily per call. Group-stage config is needed
even for knockout slots (penalty-shootout bonus uses group-stage scoring),
so we always look it up alongside the slot's own stage config.
"""

from typing import Optional

from apps.predictions.models import SlotPrediction
from apps.scoring import engine
from apps.tournament.models import ActualResult, BracketSlot, Stage


def _stage_config(stage: Stage) -> engine.StageConfig:
    return engine.StageConfig(
        points_exact=stage.points_exact,
        points_diff=stage.points_diff,
        points_result=stage.points_result,
        penalty_loser_pct=stage.penalty_loser_pct,
    )


def _build_prediction(pred: SlotPrediction) -> engine.Prediction:
    return engine.Prediction(
        round=engine.RoundConfig(
            order=pred.prediction_round.order,
            weight=pred.prediction_round.weight,
        ),
        home_team=pred.home_team.code,
        away_team=pred.away_team.code,
        home_score=pred.home_score,
        away_score=pred.away_score,
        penalty_winner=pred.penalty_winner.code if pred.penalty_winner_id else None,
        home_penalties=pred.home_penalties,
        away_penalties=pred.away_penalties,
    )


def _build_result(actual: ActualResult) -> engine.Result:
    return engine.Result(
        home_team=actual.slot.home_team_actual.code,
        away_team=actual.slot.away_team_actual.code,
        home_score=actual.home_score,
        away_score=actual.away_score,
        went_to_penalties=actual.went_to_penalties,
        penalty_winner=actual.penalty_winner.code if actual.penalty_winner_id else None,
        home_penalties=actual.home_penalties,
        away_penalties=actual.away_penalties,
    )


def score_slot_for_user(user, slot: BracketSlot) -> Optional[engine.ScoreBreakdown]:
    """Compute the score breakdown for one user on one slot.

    Returns None if the slot has no actual result yet — caller stores that as
    `matchup_type = "no_result"` in SlotScore. Returns a "no_prediction"
    breakdown if the user never predicted the slot (engine handles this).
    """
    actual = ActualResult.objects.filter(slot=slot).select_related(
        "slot__home_team_actual", "slot__away_team_actual", "penalty_winner",
    ).first()
    if actual is None:
        return None

    # Group-stage config is needed for the penalty-shootout bonus calculation
    # even on knockout slots — fetch both.
    group_stage = Stage.objects.get(tournament=slot.tournament, kind=Stage.GROUP)
    stage_cfg = _stage_config(slot.stage)
    group_cfg = _stage_config(group_stage)

    preds = (
        SlotPrediction.objects
        .filter(user=user, slot=slot)
        .select_related("home_team", "away_team", "penalty_winner", "prediction_round")
    )
    engine_preds = [_build_prediction(p) for p in preds]
    return engine.score_slot(engine_preds, _build_result(actual), stage_cfg, group_cfg)
