"""Match scoring: exact / diff / result / miss + matchup correctness."""

from decimal import Decimal

import pytest

from apps.scoring.engine import Prediction, Result, score_slot


# ---------- Helpers ----------

def _bra_arg_actual(home_score: int, away_score: int, **kwargs) -> Result:
    return Result(home_team="BRA", away_team="ARG", home_score=home_score, away_score=away_score, **kwargs)


def _pred(round_, home="BRA", away="ARG", h=0, a=0, **kwargs) -> Prediction:
    return Prediction(round=round_, home_team=home, away_team=away, home_score=h, away_score=a, **kwargs)


# ---------- Exact score ----------

def test_exact_score_full_weight(stage_group, round_pre):
    actual = _bra_arg_actual(2, 1)
    breakdown = score_slot([_pred(round_pre, h=2, a=1)], actual, stage_group, stage_group)
    assert breakdown.matchup_type == "exact"
    assert breakdown.points_match == Decimal("6")  # 6 × 1.00
    assert breakdown.total == Decimal("6")
    assert breakdown.earning_round_order == 0


def test_exact_score_partial_weight(stage_group, round_after_group):
    actual = _bra_arg_actual(2, 1)
    breakdown = score_slot([_pred(round_after_group, h=2, a=1)], actual, stage_group, stage_group)
    assert breakdown.matchup_type == "exact"
    assert breakdown.points_match == Decimal("5.10")  # 6 × 0.85


def test_exact_zero_zero_draw(stage_group, round_pre):
    actual = _bra_arg_actual(0, 0)
    breakdown = score_slot([_pred(round_pre, h=0, a=0)], actual, stage_group, stage_group)
    assert breakdown.matchup_type == "exact"
    assert breakdown.points_match == Decimal("6")


# ---------- Diff (correct outcome + correct goal difference) ----------

def test_diff_home_win(stage_group, round_pre):
    actual = _bra_arg_actual(3, 1)  # home win by 2
    breakdown = score_slot([_pred(round_pre, h=2, a=0)], actual, stage_group, stage_group)  # also home win by 2
    assert breakdown.matchup_type == "diff"
    assert breakdown.points_match == Decimal("4")


def test_diff_away_win(stage_group, round_pre):
    actual = _bra_arg_actual(1, 3)  # away win by 2
    breakdown = score_slot([_pred(round_pre, h=0, a=2)], actual, stage_group, stage_group)  # also away win by 2
    assert breakdown.matchup_type == "diff"
    assert breakdown.points_match == Decimal("4")


def test_diff_draw_with_different_score(stage_group, round_pre):
    # Both predictions are draws but different scores → diff (same goal difference of 0)
    actual = _bra_arg_actual(2, 2)
    breakdown = score_slot([_pred(round_pre, h=1, a=1)], actual, stage_group, stage_group)
    assert breakdown.matchup_type == "diff"
    assert breakdown.points_match == Decimal("4")


# ---------- Result (correct outcome only) ----------

def test_result_home_win_wrong_diff(stage_group, round_pre):
    actual = _bra_arg_actual(3, 1)  # home win by 2
    breakdown = score_slot([_pred(round_pre, h=1, a=0)], actual, stage_group, stage_group)  # home win by 1 — wrong diff
    assert breakdown.matchup_type == "result"
    assert breakdown.points_match == Decimal("2")


def test_result_higher_stage_points(stage_qf, round_pre):
    actual = _bra_arg_actual(2, 0)
    breakdown = score_slot([_pred(round_pre, h=1, a=0)], actual, stage_qf, stage_qf)
    assert breakdown.matchup_type == "result"
    assert breakdown.points_match == Decimal("5")  # QF points_result


# ---------- Miss (wrong outcome on correct matchup) ----------

def test_miss_wrong_outcome(stage_group, round_pre):
    actual = _bra_arg_actual(2, 1)  # home win
    breakdown = score_slot([_pred(round_pre, h=0, a=2)], actual, stage_group, stage_group)  # away win
    assert breakdown.matchup_type == "miss"
    assert breakdown.points_match == Decimal("0")
    assert breakdown.total == Decimal("0")
    # earning_round_order can be None on miss (no points earned)
    assert breakdown.earning_round_order is None or breakdown.earning_round_order == 0


def test_miss_predicted_draw_actual_win(stage_group, round_pre):
    actual = _bra_arg_actual(2, 1)
    breakdown = score_slot([_pred(round_pre, h=1, a=1)], actual, stage_group, stage_group)
    assert breakdown.matchup_type == "miss"


# ---------- Matchup orientation strict ----------

def test_swapped_home_away_is_wrong_matchup(stage_group, round_pre):
    """User predicted ARG vs BRA but actual is BRA vs ARG. Strict rule = no points."""
    actual = _bra_arg_actual(2, 1)
    breakdown = score_slot([_pred(round_pre, home="ARG", away="BRA", h=1, a=2)], actual, stage_group, stage_group)
    assert breakdown.matchup_type == "miss"
    assert breakdown.total == Decimal("0")
    assert breakdown.earning_round_order is None


def test_completely_wrong_teams(stage_group, round_pre):
    actual = _bra_arg_actual(2, 1)
    breakdown = score_slot(
        [_pred(round_pre, home="GER", away="FRA", h=2, a=1)],
        actual, stage_group, stage_group,
    )
    assert breakdown.matchup_type == "miss"
    assert breakdown.earning_round_order is None


# ---------- No prediction ----------

def test_no_prediction_returns_no_prediction(stage_group):
    actual = _bra_arg_actual(2, 1)
    breakdown = score_slot([], actual, stage_group, stage_group)
    assert breakdown.matchup_type == "no_prediction"
    assert breakdown.total == Decimal("0")
    assert breakdown.earning_round_order is None
