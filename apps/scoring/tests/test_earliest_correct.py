"""Multi-round earliest-correct-matchup lookup."""

from decimal import Decimal

from apps.scoring.engine import Prediction, Result, score_slot


def _actual() -> Result:
    return Result(home_team="BRA", away_team="ARG", home_score=2, away_score=1)


def test_earliest_correct_matchup_used_even_if_later_round_is_better(
    stage_group, round_pre, round_after_group, round_after_r32
):
    """Round 0 has correct matchup with imperfect score; Rounds 1 and 2 have
    exact score but later — engine uses Round 0 (earliest correct) and ignores
    later improvements. Round 0's prediction earns "diff" (correct outcome +
    correct goal difference of 1), NOT "exact".
    """
    preds = [
        Prediction(round=round_pre, home_team="BRA", away_team="ARG", home_score=3, away_score=2),
        Prediction(round=round_after_group, home_team="BRA", away_team="ARG", home_score=2, away_score=1),
        Prediction(round=round_after_r32, home_team="BRA", away_team="ARG", home_score=2, away_score=1),
    ]
    breakdown = score_slot(preds, _actual(), stage_group, stage_group)
    assert breakdown.earning_round_order == 0          # Round 0, NOT 1 or 2
    assert breakdown.matchup_type == "diff"            # 3-2 prediction = home win + diff +1, same as actual
    assert breakdown.points_match == Decimal("4")      # 4 × 1.00


def test_later_round_used_when_earlier_round_has_wrong_matchup(
    stage_group, round_pre, round_after_group, round_after_r32
):
    """Round 0 wrong teams; Rounds 1 and 2 correct. Engine uses Round 1."""
    preds = [
        Prediction(round=round_pre, home_team="GER", away_team="FRA", home_score=2, away_score=1),
        Prediction(round=round_after_group, home_team="BRA", away_team="ARG", home_score=2, away_score=1),
        Prediction(round=round_after_r32, home_team="BRA", away_team="ARG", home_score=3, away_score=0),
    ]
    breakdown = score_slot(preds, _actual(), stage_group, stage_group)
    assert breakdown.earning_round_order == 1
    assert breakdown.matchup_type == "exact"
    assert breakdown.points_match == Decimal("5.10")   # 6 × 0.85


def test_no_correct_matchup_in_any_round_returns_miss(
    stage_group, round_pre, round_after_group
):
    preds = [
        Prediction(round=round_pre, home_team="GER", away_team="FRA", home_score=2, away_score=1),
        Prediction(round=round_after_group, home_team="ESP", away_team="POR", home_score=1, away_score=0),
    ]
    breakdown = score_slot(preds, _actual(), stage_group, stage_group)
    assert breakdown.matchup_type == "miss"
    assert breakdown.earning_round_order is None
    assert breakdown.total == Decimal("0")


def test_unsorted_predictions_handled(stage_group, round_pre, round_after_group, round_after_r32):
    """Engine sorts predictions internally — order in the iterable shouldn't matter."""
    preds = [
        # Intentionally in reverse round order
        Prediction(round=round_after_r32, home_team="BRA", away_team="ARG", home_score=2, away_score=1),
        Prediction(round=round_after_group, home_team="BRA", away_team="ARG", home_score=2, away_score=1),
        Prediction(round=round_pre, home_team="BRA", away_team="ARG", home_score=2, away_score=0),  # home win, diff +2 — wrong diff
    ]
    breakdown = score_slot(preds, _actual(), stage_group, stage_group)
    assert breakdown.earning_round_order == 0
    assert breakdown.matchup_type == "result"          # home win correct, diff wrong


def test_only_late_round_correct(stage_group, round_pre, round_after_r16):
    """User joins late, only Round 3 has a correct matchup."""
    preds = [
        Prediction(round=round_pre, home_team="GER", away_team="FRA", home_score=1, away_score=2),
        Prediction(round=round_after_r16, home_team="BRA", away_team="ARG", home_score=2, away_score=1),
    ]
    breakdown = score_slot(preds, _actual(), stage_group, stage_group)
    assert breakdown.earning_round_order == 3
    assert breakdown.matchup_type == "exact"
    assert breakdown.points_match == Decimal("3.90")   # 6 × 0.65
