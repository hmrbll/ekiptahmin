"""Pure-engine tests for `potential_max_scores` — the pre-result "best case"
payout shown on the all-predictions page for not-yet-scored matches.

Exercises apps/scoring/ganyan.py directly (no DB): a pick's best case is the
parimutuel payout it would earn if the match ended exactly as predicted, each
pool split among everyone whose own pick would also win it.
"""

from decimal import Decimal

from apps.scoring.ganyan import Prediction, StagePools, potential_max_scores

W = Decimal("1.00")


def _pools(reg=100, pen=50):
    return StagePools(
        pool_exact=reg, pool_diff=reg, pool_result=reg,
        pool_penalty_winner=pen, pool_penalty_score=pen, pool_penalty_diff=pen,
    )


def _pred(uid_order=0, weight=W, home="BRA", away="ARG", h=2, a=1, **kw):
    base = dict(
        round_order=uid_order, round_weight=weight,
        home_team=home, away_team=away, home_score=h, away_score=a,
    )
    base.update(kw)
    return Prediction(**base)


def test_sole_winner_takes_full_regulation_pools():
    """One predictor: exact+diff+result, undivided → 300 with 100/100/100."""
    out = potential_max_scores({1: _pred()}, _pools(), "BRA", "ARG")
    assert out[1] == Decimal("300")


def test_identical_picks_split_each_pool():
    """Two identical picks halve every pool they share → 150 each."""
    out = potential_max_scores(
        {1: _pred(), 2: _pred()}, _pools(), "BRA", "ARG",
    )
    assert out[1] == Decimal("150")
    assert out[2] == Decimal("150")


def test_same_result_only_splits_the_result_pool():
    """2-1 vs 3-0: distinct exact and diff (sole each), shared result (home win).
    Each → 100 + 100 + 50 = 250."""
    out = potential_max_scores(
        {1: _pred(h=2, a=1), 2: _pred(h=3, a=0)}, _pools(), "BRA", "ARG",
    )
    assert out[1] == Decimal("250")
    assert out[2] == Decimal("250")


def test_wrong_matchup_is_dropped():
    """A pick on a different fixture can never score → absent from the map."""
    out = potential_max_scores(
        {1: _pred(home="BRA", away="ARG"), 2: _pred(home="BRA", away="URU")},
        _pools(), "BRA", "ARG",
    )
    assert 1 in out
    assert 2 not in out
    assert out[1] == Decimal("300")  # user 2 isn't a co-winner of anything


def test_round_weight_scales_the_best_case():
    """A lower-weight round pays proportionally less in the best case."""
    out = potential_max_scores(
        {1: _pred(weight=Decimal("0.50"))}, _pools(), "BRA", "ARG",
    )
    assert out[1] == Decimal("150.00")  # 300 × 0.50


def test_draw_on_ko_adds_penalty_pools():
    """A draw-on-KO pick carrying a shootout wins all six pools in its best
    case: 100×3 regulation + 50×3 penalty = 450."""
    draw = _pred(h=1, a=1, penalty_winner="BRA", home_penalties=4, away_penalties=2)
    out = potential_max_scores({1: draw}, _pools(), "BRA", "ARG")
    assert out[1] == Decimal("450")


def test_decisive_pick_never_claims_penalty_pools():
    """A decisive scoreline implies no shootout → only the regulation pools."""
    out = potential_max_scores({1: _pred(h=2, a=1)}, _pools(), "BRA", "ARG")
    assert out[1] == Decimal("300")
