"""N (PoolStats.predictor_count) counts only picks on the actual fixture.

A knockout slot collects predictions from everyone's bracket, but a pick on a
different matchup didn't predict *this* match — it's excluded from both the
breakdown and N. Pure-engine test (no DB).
"""

from decimal import Decimal

from apps.scoring.ganyan import Prediction, Result, StagePools, compute_slot

W = Decimal("1.00")


def _pools():
    return StagePools(
        pool_exact=100, pool_diff=100, pool_result=100,
        pool_penalty_winner=50, pool_penalty_score=50, pool_penalty_diff=50,
        pool_advancer=50,
    )


def _pred(home_team, away_team, h, a):
    return Prediction(
        round_order=0, round_weight=W,
        home_team=home_team, away_team=away_team, home_score=h, away_score=a,
    )


def test_predictor_count_excludes_wrong_matchup():
    result = Result(home_team="BRA", away_team="ECU", home_score=3, away_score=0)
    preds = {
        1: [_pred("BRA", "ECU", 3, 0)],   # this fixture
        2: [_pred("BRA", "ECU", 2, 0)],   # this fixture
        3: [_pred("BRA", "ECU", 1, 0)],   # this fixture
        4: [_pred("USA", "MEX", 2, 1)],   # different bracket → not this match
        5: [_pred("KOR", "BIH", 0, 0)],   # different bracket → not this match
    }
    _scores, stats = compute_slot(preds, result, _pools())
    by_c = {s.criterion: s for s in stats}
    # 5 users have a pick on this slot, but only 3 predicted THIS fixture.
    assert all(s.predictor_count == 3 for s in stats)
    # N stays consistent with the breakdown (exact has a key per matchup pick).
    assert sum(by_c["exact"].breakdown.values()) == 3
