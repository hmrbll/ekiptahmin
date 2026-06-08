"""Pure-engine tests for the three ganyan penalty criteria.

penalty_winner — named the team that advanced (open to any prediction).
penalty_score  — exact shootout score (draw predictions only).
penalty_diff   — shootout goal difference, signed home−away (draw predictions only).

These exercise apps/scoring/ganyan.py directly (no DB), so they pin the
shootout scoring rules independently of the ORM bridge.
"""

from decimal import Decimal

from apps.scoring import ganyan
from apps.scoring.ganyan import (
    Prediction,
    Result,
    StagePools,
    compute_slot,
    satisfies_penalty_diff,
    satisfies_penalty_score,
    satisfies_penalty_winner,
)

W = Decimal("1.00")


def _pools(reg=100, pen=50):
    return StagePools(
        pool_exact=reg, pool_diff=reg, pool_result=reg,
        pool_penalty_winner=pen, pool_penalty_score=pen, pool_penalty_diff=pen,
    )


def _result(**kw):
    base = dict(
        home_team="BRA", away_team="ARG", home_score=1, away_score=1,
        went_to_penalties=True, penalty_winner="BRA",
        home_penalties=4, away_penalties=2,
    )
    base.update(kw)
    return Result(**base)


def _pred(home_score=1, away_score=1, **kw):
    base = dict(
        round_order=0, round_weight=W, home_team="BRA", away_team="ARG",
        home_score=home_score, away_score=away_score,
    )
    base.update(kw)
    return Prediction(**base)


# ---------- satisfies_penalty_winner ----------

def test_winner_draw_predictor_correct():
    p = _pred(1, 1, penalty_winner="BRA", home_penalties=4, away_penalties=2)
    assert satisfies_penalty_winner(p, _result()) is True


def test_winner_nondraw_implied_advancer_correct():
    # Predicted BRA to win 2-1 (regulation wrong: 1-1) but BRA advanced on pens.
    p = _pred(2, 1)
    assert satisfies_penalty_winner(p, _result()) is True


def test_winner_wrong_team():
    p = _pred(1, 1, penalty_winner="ARG", home_penalties=2, away_penalties=4)
    assert satisfies_penalty_winner(p, _result()) is False


def test_winner_false_when_no_shootout():
    p = _pred(2, 1)
    assert satisfies_penalty_winner(p, _result(went_to_penalties=False, penalty_winner=None)) is False


# ---------- satisfies_penalty_score / diff ----------

def test_score_and_diff_exact_match():
    p = _pred(1, 1, penalty_winner="BRA", home_penalties=4, away_penalties=2)
    r = _result(home_penalties=4, away_penalties=2)
    assert satisfies_penalty_score(p, r) is True
    assert satisfies_penalty_diff(p, r) is True


def test_diff_without_exact_score():
    # Predicted 5-4 (diff +1); actual 4-3 (diff +1). Score wrong, diff right.
    p = _pred(0, 0, penalty_winner="BRA", home_penalties=5, away_penalties=4)
    r = _result(home_score=0, away_score=0, home_penalties=4, away_penalties=3)
    assert satisfies_penalty_score(p, r) is False
    assert satisfies_penalty_diff(p, r) is True


def test_nondraw_predictor_cannot_win_score_or_diff():
    # Non-draw prediction carries no shootout score → can't win score/diff,
    # but can still win the winner pool via the implied advancer.
    p = _pred(2, 1)  # no home_penalties/away_penalties
    r = _result()
    assert satisfies_penalty_score(p, r) is False
    assert satisfies_penalty_diff(p, r) is False
    assert satisfies_penalty_winner(p, r) is True


# ---------- compute_slot integration ----------

def test_penalty_pools_split_among_winners():
    # Two users both: regulation miss (predicted 2-0, actual 1-1) but both named
    # BRA as advancer with no shootout score → only penalty_winner pool pays,
    # split 50/50 = 25 each. score/diff pools burn (no draw predictors).
    preds = {
        1: [_pred(2, 0)],
        2: [_pred(3, 0)],
    }
    scores, stats = compute_slot(preds, _result(), _pools())

    for uid in (1, 2):
        s = scores[uid]
        assert s.score_penalty == Decimal("25")     # 50 / 2 winners
        assert s.total == Decimal("25")
        assert s.outcome == ganyan.OUTCOME_PENALTY   # penalty-only badge

    by_c = {st.criterion: st for st in stats}
    assert by_c["penalty_winner"].winner_count == 2
    assert by_c["penalty_winner"].base_payout == Decimal("25")
    # No draw predictors → score/diff pools have no winners (burned).
    assert by_c["penalty_score"].winner_count == 0
    assert by_c["penalty_score"].base_payout is None


def test_draw_predictor_sweeps_all_penalty_pools():
    # Exact 1-1 + exact shootout 4-2 → wins every regulation + penalty pool solo.
    preds = {1: [_pred(1, 1, penalty_winner="BRA", home_penalties=4, away_penalties=2)]}
    scores, _ = compute_slot(preds, _result(), _pools())
    s = scores[1]
    # Regulation: exact+diff+result = 100+100+100. Penalty: winner+score+diff = 50*3.
    assert s.score_exact == Decimal("100")
    assert s.score_penalty == Decimal("150")
    assert s.total == Decimal("450")
    assert s.outcome == ganyan.OUTCOME_EXACT  # regulation tier wins the badge
