"""Pure-engine tests for the ganyan shootout criteria.

penalty_winner — named the shootout winner (draw/shootout predictions ONLY).
penalty_score  — exact shootout score (draw predictions only).
penalty_diff   — shootout goal difference, signed home−away (draw predictions only).
advancer       — named the advancing team ("turlayan"); open to any prediction
                 on the fixture (implied winner for decisive picks, chosen
                 shootout winner for draw picks). Pens matches only.

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
    satisfies_advancer,
    satisfies_penalty_diff,
    satisfies_penalty_score,
    satisfies_penalty_winner,
)

W = Decimal("1.00")


def _pools(reg=100, pen=50, adv=50):
    return StagePools(
        pool_exact=reg, pool_diff=reg, pool_result=reg,
        pool_penalty_winner=pen, pool_penalty_score=pen, pool_penalty_diff=pen,
        pool_advancer=adv,
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


def test_winner_closed_to_nondraw_predictions():
    # Predicted BRA to win 2-1 — the implied advancer is right, but a decisive
    # pick didn't predict the shootout, so the penalty_winner pool is closed to
    # it. It competes in the advancer pool instead.
    p = _pred(2, 1)
    assert satisfies_penalty_winner(p, _result()) is False
    assert satisfies_advancer(p, _result()) is True


def test_winner_wrong_team():
    p = _pred(1, 1, penalty_winner="ARG", home_penalties=2, away_penalties=4)
    assert satisfies_penalty_winner(p, _result()) is False


def test_winner_false_when_no_shootout():
    r = _result(went_to_penalties=False, penalty_winner=None)
    assert satisfies_penalty_winner(_pred(2, 1), r) is False
    assert satisfies_advancer(_pred(2, 1), r) is False


# ---------- satisfies_advancer ----------

def test_advancer_open_to_draw_and_decisive_predictions():
    r = _result()
    # Draw pick advancing BRA via its chosen shootout winner.
    assert satisfies_advancer(_pred(1, 1, penalty_winner="BRA"), r) is True
    # Decisive pick advancing BRA via its scoreline.
    assert satisfies_advancer(_pred(2, 1), r) is True
    # Wrong advancer either way.
    assert satisfies_advancer(_pred(1, 1, penalty_winner="ARG"), r) is False
    assert satisfies_advancer(_pred(0, 1), r) is False


def test_advancer_requires_matchup():
    p = _pred(2, 1, home_team="BRA", away_team="GER")
    assert satisfies_advancer(p, _result()) is False


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


def test_nondraw_predictor_only_reaches_the_advancer_pool():
    # Non-draw prediction carries no shootout data → every shootout-only pool
    # is closed to it; the advancer pool is its only pens-path payout.
    p = _pred(2, 1)
    r = _result()
    assert satisfies_penalty_score(p, r) is False
    assert satisfies_penalty_diff(p, r) is False
    assert satisfies_penalty_winner(p, r) is False
    assert satisfies_advancer(p, r) is True


# ---------- compute_slot integration ----------

def test_advancer_pool_splits_among_all_correct_advancers():
    # Two users: regulation miss (predicted 2-0 / 3-0, actual 1-1) but both have
    # BRA advancing via their scoreline → they split ONLY the advancer pool
    # (50/2 = 25 each). Every shootout-only pool burns (no draw predictors).
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
    assert by_c["advancer"].winner_count == 2
    assert by_c["advancer"].base_payout == Decimal("25")
    # No draw predictors → all shootout-only pools have no winners (burned).
    assert by_c["penalty_winner"].winner_count == 0
    assert by_c["penalty_winner"].base_payout is None
    assert by_c["penalty_score"].winner_count == 0
    assert by_c["penalty_score"].base_payout is None


def test_shootout_pools_closed_to_decisive_pick_open_advancer_shared():
    # User 1 predicted the shootout (1-1, BRA on pens); user 2 predicted BRA
    # decisively (2-1). penalty_winner pays user 1 alone; advancer splits
    # between both; score/diff pay user 1 alone (exact shootout).
    preds = {
        1: [_pred(1, 1, penalty_winner="BRA", home_penalties=4, away_penalties=2)],
        2: [_pred(2, 1)],
    }
    scores, stats = compute_slot(preds, _result(), _pools())

    by_c = {st.criterion: st for st in stats}
    assert by_c["penalty_winner"].winner_count == 1
    assert by_c["penalty_winner"].base_payout == Decimal("50")
    assert by_c["advancer"].winner_count == 2
    assert by_c["advancer"].base_payout == Decimal("25")

    # User 1: shootout-predictor — winner 50 + score 50 + diff 50 + advancer 25.
    assert scores[1].score_penalty == Decimal("175")
    # User 2: decisive — advancer slice only.
    assert scores[2].score_penalty == Decimal("25")
    assert scores[2].outcome == ganyan.OUTCOME_PENALTY

    # penalty_winner breakdown lists only the shootout prediction; advancer both.
    assert by_c["penalty_winner"].breakdown == {"BRA": 1}
    assert by_c["advancer"].breakdown == {"BRA": 2}


def test_draw_predictor_sweeps_all_penalty_pools():
    # Exact 1-1 + exact shootout 4-2 → wins every regulation + shootout pool solo.
    preds = {1: [_pred(1, 1, penalty_winner="BRA", home_penalties=4, away_penalties=2)]}
    scores, _ = compute_slot(preds, _result(), _pools())
    s = scores[1]
    # Regulation: exact+diff+result = 100+100+100.
    # Shootout: winner+score+diff+advancer = 50*3 + 50.
    assert s.score_exact == Decimal("100")
    assert s.score_penalty == Decimal("200")
    assert s.total == Decimal("500")
    assert s.outcome == ganyan.OUTCOME_EXACT  # regulation tier wins the badge
