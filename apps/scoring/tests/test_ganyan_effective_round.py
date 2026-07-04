"""Effective-round selection is decoupled from pool dilution.

Each user's effective round is the one that maximizes their weighted total
against the NOMINAL pools (every criterion at full size), depending only on the
user's own predictions. There is no fixed-point iteration: the pick never
re-optimizes against the diluted payouts. This pins that decoupling — in the
"flip" case (a later, much-lighter round holds a higher tier) the user is scored
on their nominal-best round, slightly under the theoretical post-dilution max.
Pure engine, no DB.
"""

from decimal import Decimal

from apps.scoring.ganyan import Prediction, Result, StagePools, compute_slot


def _pools():
    return StagePools(
        pool_exact=100, pool_diff=100, pool_result=100,
        pool_penalty_winner=50, pool_penalty_score=50, pool_penalty_diff=50,
        pool_advancer=50,
    )


def _p(order, w, h, a):
    return Prediction(
        round_order=order, round_weight=Decimal(str(w)),
        home_team="BRA", away_team="ECU", home_score=h, away_score=a,
    )


def test_effective_round_is_nominal_best_not_dilution_best():
    """Hemre's early round (2-1, diff, weight 1.0) outscores his late round
    (1-0, exact, weight 0.6) at NOMINAL pools (200 vs 180), so it is his
    effective round — even though, post-dilution, the late round could have paid
    him marginally more by joining Ada in the exact pool.

    result 1-0; pools 100/100/100.
      Ada    1-0 (exact)            w=1.0
      Hemre  2-1 (diff) | 1-0 (exact)  w=1.0 | 0.6
      three others: result-only
    """
    result = Result(home_team="BRA", away_team="ECU", home_score=1, away_score=0)
    preds = {
        1: [_p(0, 1.0, 1, 0)],                    # Ada — exact
        2: [_p(0, 1.0, 2, 1), _p(5, 0.6, 1, 0)],  # Hemre — early diff vs late exact
        3: [_p(0, 1.0, 2, 0)],                    # result-only
        4: [_p(0, 1.0, 3, 0)],                    # result-only
        5: [_p(0, 1.0, 4, 0)],                    # result-only
    }
    scores, stats = compute_slot(preds, result, _pools())

    # Hemre scored on his EARLY round (nominal-best), not the late exact one.
    assert scores[2].effective_round_order == 0
    assert scores[2].outcome == "diff"
    # diff (W=2: Ada+Hemre) = 50, result (W=5) = 20 → (50+20)×1.0 = 70.
    assert scores[2].total == Decimal("70")

    # Hemre never joins the exact pool, so Ada keeps it solo (100, not 50).
    by_c = {s.criterion: s for s in stats}
    assert by_c["exact"].winner_count == 1
    assert by_c["exact"].base_payout == Decimal("100")
    # Ada: exact 100 + diff 50 + result 20 = 170.
    assert scores[1].total == Decimal("170")


def test_no_iteration_param():
    """compute_slot is a single pass — it takes no max_iterations knob."""
    import inspect

    params = inspect.signature(compute_slot).parameters
    assert "max_iterations" not in params


def test_missed_on_fixture_pick_beats_earlier_off_fixture_tie():
    """A user who predicted THIS match but missed is attributed to the real
    fixture, not to an earlier off-fixture bracket pick they also hold.

    Knockout slot BRA-JPN, result 2-1. Hemre's pre-tournament bracket had
    BRA-SWE (a different pairing → 0), then he predicted the real BRA-JPN as a
    1-1 draw (wrong result → also 0). Both candidates score 0; the tie must fall
    to the on-fixture round so he counts toward N and shows as a 'miss', instead
    of vanishing behind the off-fixture pick.
    """
    result = Result(home_team="BRA", away_team="JPN", home_score=2, away_score=1)

    def _pp(order, w, h, a, home, away):
        return Prediction(
            round_order=order, round_weight=Decimal(str(w)),
            home_team=home, away_team=away, home_score=h, away_score=a,
        )

    preds = {
        # Hemre: early off-fixture (BRA-SWE) + later on-fixture miss (BRA-JPN 1-1).
        1: [_pp(0, 1.0, 2, 0, "BRA", "SWE"), _pp(1, 0.85, 1, 1, "BRA", "JPN")],
        # An on-fixture hitter so the pools aren't degenerate.
        2: [_pp(0, 1.0, 2, 1, "BRA", "JPN")],
    }
    scores, stats = compute_slot(preds, result, _pools())

    # Effective round is the on-fixture one (order 1), not the off-fixture order 0.
    assert scores[1].effective_round_order == 1
    assert scores[1].outcome == "miss"
    assert scores[1].total == Decimal("0")
    # He now counts among the match's predictors (N), alongside the hitter.
    by_c = {s.criterion: s for s in stats}
    assert by_c["result"].predictor_count == 2
    # His 1-1 lands in the regulation breakdowns (proof he's attributed here).
    assert by_c["exact"].breakdown.get("1-1") == 1
