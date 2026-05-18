"""Multi-round best-round lookup.

The engine picks the round that yields the highest weighted total among rounds
where the matchup is correct. Ties go to the earlier round.
"""

from decimal import Decimal

from apps.scoring.engine import Prediction, Result, score_slot


def _actual() -> Result:
    return Result(home_team="BRA", away_team="ARG", home_score=2, away_score=1)


def test_later_round_with_better_score_wins(
    stage_group, round_pre, round_after_group, round_after_r32
):
    """Round 0 has correct matchup but wrong outcome (miss = 0). Round 1 has
    exact score (6 × 0.85 = 5.10). Round 2 also exact (6 × 0.75 = 4.50). Best
    is Round 1.
    """
    preds = [
        Prediction(round=round_pre, home_team="BRA", away_team="ARG", home_score=0, away_score=3),
        Prediction(round=round_after_group, home_team="BRA", away_team="ARG", home_score=2, away_score=1),
        Prediction(round=round_after_r32, home_team="BRA", away_team="ARG", home_score=2, away_score=1),
    ]
    breakdown = score_slot(preds, _actual(), stage_group, stage_group)
    assert breakdown.earning_round_order == 1
    assert breakdown.matchup_type == "exact"
    assert breakdown.points_match == Decimal("5.10")  # 6 × 0.85


def test_earlier_round_wins_when_same_classification(
    stage_group, round_pre, round_after_group
):
    """Same matchup_type in both rounds → earlier wins because weight is
    higher (1.00 > 0.85). 'Erken bilen kazanır' preserved when scoring
    quality is equal.
    """
    preds = [
        Prediction(round=round_pre, home_team="BRA", away_team="ARG", home_score=2, away_score=1),
        Prediction(round=round_after_group, home_team="BRA", away_team="ARG", home_score=2, away_score=1),
    ]
    breakdown = score_slot(preds, _actual(), stage_group, stage_group)
    assert breakdown.earning_round_order == 0
    assert breakdown.matchup_type == "exact"
    assert breakdown.points_match == Decimal("6.00")  # 6 × 1.00


def test_later_round_used_when_earlier_round_has_wrong_matchup(
    stage_group, round_pre, round_after_group, round_after_r32
):
    """Round 0 wrong teams; Rounds 1 and 2 correct with same score. Best is
    Round 1 (higher weight)."""
    preds = [
        Prediction(round=round_pre, home_team="GER", away_team="FRA", home_score=2, away_score=1),
        Prediction(round=round_after_group, home_team="BRA", away_team="ARG", home_score=2, away_score=1),
        Prediction(round=round_after_r32, home_team="BRA", away_team="ARG", home_score=3, away_score=0),
    ]
    breakdown = score_slot(preds, _actual(), stage_group, stage_group)
    assert breakdown.earning_round_order == 1
    assert breakdown.matchup_type == "exact"
    assert breakdown.points_match == Decimal("5.10")  # 6 × 0.85


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
        # Intentionally in reverse round order; Round 0 has the best score.
        Prediction(round=round_after_r32, home_team="BRA", away_team="ARG", home_score=2, away_score=0),  # result
        Prediction(round=round_after_group, home_team="BRA", away_team="ARG", home_score=3, away_score=2),  # diff
        Prediction(round=round_pre, home_team="BRA", away_team="ARG", home_score=2, away_score=1),  # exact
    ]
    breakdown = score_slot(preds, _actual(), stage_group, stage_group)
    assert breakdown.earning_round_order == 0
    assert breakdown.matchup_type == "exact"
    assert breakdown.points_match == Decimal("6.00")


def test_only_late_round_correct(stage_group, round_pre, round_after_r16):
    """User joins late, only Round 3 has a correct matchup."""
    preds = [
        Prediction(round=round_pre, home_team="GER", away_team="FRA", home_score=1, away_score=2),
        Prediction(round=round_after_r16, home_team="BRA", away_team="ARG", home_score=2, away_score=1),
    ]
    breakdown = score_slot(preds, _actual(), stage_group, stage_group)
    assert breakdown.earning_round_order == 3
    assert breakdown.matchup_type == "exact"
    assert breakdown.points_match == Decimal("3.90")  # 6 × 0.65


def test_late_round_exact_beats_early_round_miss(
    stage_sf, round_pre, round_after_r16
):
    """Hemre's SF scenario: pre-round had correct matchup but flipped outcome
    (1-0 instead of 0-1), late round nailed the exact score. Late round wins.

    SF stage: points_exact=20. Round 3 weight=0.65 → 13.00.
    """
    actual = Result(home_team="BRA", away_team="ARG", home_score=0, away_score=1)
    preds = [
        Prediction(round=round_pre, home_team="BRA", away_team="ARG", home_score=1, away_score=0),
        Prediction(round=round_after_r16, home_team="BRA", away_team="ARG", home_score=0, away_score=1),
    ]
    breakdown = score_slot(preds, actual, stage_sf, stage_sf)
    assert breakdown.earning_round_order == 3
    assert breakdown.matchup_type == "exact"
    assert breakdown.points_match == Decimal("13.00")
    assert breakdown.total == Decimal("13.00")


def test_early_round_diff_beats_late_round_result(
    stage_group, round_pre, round_after_r32
):
    """Pre-round: correct outcome + diff (4 × 1.00 = 4.00).
    Late round: correct outcome only (2 × 0.75 = 1.50). Pre wins.
    """
    preds = [
        Prediction(round=round_pre, home_team="BRA", away_team="ARG", home_score=3, away_score=2),
        Prediction(round=round_after_r32, home_team="BRA", away_team="ARG", home_score=5, away_score=0),
    ]
    breakdown = score_slot(preds, _actual(), stage_group, stage_group)
    assert breakdown.earning_round_order == 0
    assert breakdown.matchup_type == "diff"
    assert breakdown.points_match == Decimal("4.00")
