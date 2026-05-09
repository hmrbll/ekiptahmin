"""Round-weight application + decimal precision."""

from decimal import Decimal

from apps.scoring.engine import Prediction, Result, RoundConfig, score_slot


def _actual_2_1() -> Result:
    return Result(home_team="BRA", away_team="ARG", home_score=2, away_score=1)


def test_weight_one_no_change(stage_group):
    round_ = RoundConfig(order=0, weight=Decimal("1.00"))
    pred = Prediction(round=round_, home_team="BRA", away_team="ARG", home_score=2, away_score=1)
    breakdown = score_slot([pred], _actual_2_1(), stage_group, stage_group)
    assert breakdown.points_match == Decimal("6")


def test_weight_half_halves_points(stage_qf):
    round_ = RoundConfig(order=4, weight=Decimal("0.50"))
    pred = Prediction(round=round_, home_team="BRA", away_team="ARG", home_score=2, away_score=1)
    breakdown = score_slot([pred], _actual_2_1(), stage_qf, stage_qf)
    assert breakdown.points_match == Decimal("7.00")  # QF exact = 14, × 0.50


def test_decimal_precision_preserved(stage_sf):
    round_ = RoundConfig(order=5, weight=Decimal("0.55"))
    pred = Prediction(round=round_, home_team="BRA", away_team="ARG", home_score=2, away_score=1)
    breakdown = score_slot([pred], _actual_2_1(), stage_sf, stage_sf)
    assert breakdown.points_match == Decimal("11.00")  # 20 × 0.55


def test_diff_with_weight(stage_r16):
    round_ = RoundConfig(order=2, weight=Decimal("0.75"))
    actual = Result(home_team="BRA", away_team="ARG", home_score=3, away_score=1)
    pred = Prediction(round=round_, home_team="BRA", away_team="ARG", home_score=2, away_score=0)
    breakdown = score_slot([pred], actual, stage_r16, stage_r16)
    assert breakdown.matchup_type == "diff"
    assert breakdown.points_match == Decimal("4.50")  # 6 (R16 diff) × 0.75
