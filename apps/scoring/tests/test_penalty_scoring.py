"""Penalty shootout: loser bonus + draw-predictor's penalty bonus."""

from decimal import Decimal

from apps.scoring.engine import Prediction, Result, score_slot


def _actual_pens(pred_winner: str, home_score: int = 1, away_score: int = 1, **kwargs) -> Result:
    """Helper for matches that ended in a draw at 90' and went to penalties."""
    return Result(
        home_team="BRA", away_team="ARG",
        home_score=home_score, away_score=away_score,
        went_to_penalties=True,
        penalty_winner=pred_winner,
        **kwargs,
    )


# ---------- Penalty loser bonus ----------
# User predicted a non-draw on a correct matchup; the team they picked won via penalties.
# Spec: ROUND(stage.points_result × penalty_loser_pct × weight) → integer.

def test_penalty_loser_bonus_pre_round(stage_qf, round_pre):
    actual = _actual_pens(pred_winner="BRA", home_score=1, away_score=1)
    pred = Prediction(round=round_pre, home_team="BRA", away_team="ARG", home_score=2, away_score=1)
    breakdown = score_slot([pred], actual, stage_qf, stage_qf)
    assert breakdown.matchup_type == "penalty_loser_bonus"
    # ROUND(5 × 0.60 × 1.00) = ROUND(3.00) = 3
    assert breakdown.points_match == Decimal("3")


def test_penalty_loser_bonus_with_round_weight_rounds_up(stage_qf, round_after_group):
    actual = _actual_pens(pred_winner="BRA", home_score=1, away_score=1)
    pred = Prediction(round=round_after_group, home_team="BRA", away_team="ARG", home_score=2, away_score=1)
    breakdown = score_slot([pred], actual, stage_qf, stage_qf)
    # ROUND(5 × 0.60 × 0.85) = ROUND(2.55) = 3
    assert breakdown.matchup_type == "penalty_loser_bonus"
    assert breakdown.points_match == Decimal("3")


def test_penalty_loser_bonus_wrong_team_no_bonus(stage_qf, round_pre):
    """User predicted away win, but home team won via penalties → 0."""
    actual = _actual_pens(pred_winner="BRA", home_score=1, away_score=1)
    pred = Prediction(round=round_pre, home_team="BRA", away_team="ARG", home_score=0, away_score=2)
    breakdown = score_slot([pred], actual, stage_qf, stage_qf)
    assert breakdown.matchup_type == "miss"
    assert breakdown.total == Decimal("0")


def test_penalty_loser_bonus_only_when_actual_went_to_penalties(stage_qf, round_pre):
    """Same prediction but actual didn't go to penalties → just a regular miss."""
    actual = Result(home_team="BRA", away_team="ARG", home_score=1, away_score=2)  # away wins
    pred = Prediction(round=round_pre, home_team="BRA", away_team="ARG", home_score=2, away_score=1)
    breakdown = score_slot([pred], actual, stage_qf, stage_qf)
    assert breakdown.matchup_type == "miss"


# ---------- Draw predictor's penalty bonus ----------
# When user predicted a draw and actual ended in penalties, regular score applies on the
# 90' draw, PLUS a penalty bonus based on penalty winner + score prediction.

def test_draw_predictor_gets_regular_score_plus_penalty_bonus(stage_qf, stage_group, round_pre):
    actual = Result(
        home_team="BRA", away_team="ARG",
        home_score=1, away_score=1,
        went_to_penalties=True,
        penalty_winner="BRA",
        home_penalties=4,
        away_penalties=2,
    )
    pred = Prediction(
        round=round_pre, home_team="BRA", away_team="ARG",
        home_score=1, away_score=1,
        penalty_winner="BRA",
        home_penalties=4,
        away_penalties=2,
    )
    breakdown = score_slot([pred], actual, stage_qf, stage_group)
    assert breakdown.matchup_type == "exact"        # 1-1 == 1-1 at 90'
    assert breakdown.points_match == Decimal("14")  # QF exact × 1.00
    # Penalty exact (4-2 == 4-2) using GROUP scoring (6) × 1.00
    assert breakdown.points_penalty == Decimal("6")
    assert breakdown.total == Decimal("20")


def test_draw_predictor_correct_winner_wrong_pen_score(stage_qf, stage_group, round_pre):
    actual = Result(
        home_team="BRA", away_team="ARG",
        home_score=0, away_score=0,
        went_to_penalties=True, penalty_winner="BRA",
        home_penalties=5, away_penalties=4,
    )
    pred = Prediction(
        round=round_pre, home_team="BRA", away_team="ARG",
        home_score=0, away_score=0,
        penalty_winner="BRA",
        home_penalties=4, away_penalties=2,  # right winner, wrong score, wrong diff
    )
    breakdown = score_slot([pred], actual, stage_qf, stage_group)
    assert breakdown.matchup_type == "exact"
    # Penalty diff: actual diff = 1, predicted diff = 2 → not diff. → result (winner correct) → 2
    assert breakdown.points_penalty == Decimal("2")


def test_draw_predictor_wrong_pen_winner_no_bonus(stage_qf, stage_group, round_pre):
    actual = Result(
        home_team="BRA", away_team="ARG",
        home_score=1, away_score=1,
        went_to_penalties=True, penalty_winner="BRA",
        home_penalties=5, away_penalties=4,
    )
    pred = Prediction(
        round=round_pre, home_team="BRA", away_team="ARG",
        home_score=1, away_score=1,
        penalty_winner="ARG",  # wrong
        home_penalties=4, away_penalties=5,
    )
    breakdown = score_slot([pred], actual, stage_qf, stage_group)
    assert breakdown.matchup_type == "exact"
    assert breakdown.points_match == Decimal("14")
    assert breakdown.points_penalty == Decimal("0")


def test_no_penalty_bonus_for_non_draw_predictor_who_lost(stage_qf, stage_group, round_pre):
    """Non-draw prediction with WRONG team: no loser bonus, no draw-bonus path either."""
    actual = Result(
        home_team="BRA", away_team="ARG",
        home_score=1, away_score=1,
        went_to_penalties=True, penalty_winner="ARG",
        home_penalties=2, away_penalties=4,
    )
    pred = Prediction(
        round=round_pre, home_team="BRA", away_team="ARG",
        home_score=2, away_score=0,
    )
    breakdown = score_slot([pred], actual, stage_qf, stage_group)
    assert breakdown.matchup_type == "miss"
    assert breakdown.total == Decimal("0")
