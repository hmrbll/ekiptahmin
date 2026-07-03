"""Pure-Python parimutuel ('ganyan') scoring engine.

Operates on plain dataclasses, no Django imports. The companion bridge
(`apps/scoring/ganyan_bridge.py`) loads ORM rows and calls into here.

Mechanic — for one match (BracketSlot) at a time:

1. Every user is a single entity. Multiple rounds predicting the same match
   collapse to "the user has these candidate round-predictions."
2. Each criterion has a fixed pool. Regulation criteria (exact / diff / result)
   score the 90-minute scoreline. Penalty criteria (penalty_winner /
   penalty_score / penalty_diff) score the shootout, and only apply on knockout
   matches that actually went to penalties.
3. For each user we pick ONE effective round — the one that maximizes the
   user's weighted total against the NOMINAL pools (every criterion valued at
   its full size). This depends only on the user's own predictions, so it is
   deterministic and order-independent; the effective round's prediction
   supplies the user's per-criterion payouts, other rounds don't earn.
4. base_payout_c = pool_c / |W_c|, where W_c = users whose effective round
   satisfies c. Pool burns if no one is in W_c.

Picking against the nominal (full) pools instead of the diluted payouts keeps
each user's choice decoupled from everyone else's: one pass, one unique result,
no fixed-point iteration. In the rare case where dilution would have made a
different round pay a user more (only reachable when a later, much-lighter round
holds a higher-tier prediction), that user is scored slightly under their
theoretical max — the deterministic, explainable outcome is the deliberate
trade. See docs/scoring-ganyan.md.

Outcome label (mutually exclusive, highest tier): EXACT > DIFF > RESULT >
PENALTY > MISS. The single PENALTY tier fires when a user earned from any
penalty criterion but missed all three regulation tiers. Drives the
leaderboard tiebreaker counts.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

# Regulation criteria score the 90' scoreline; penalty criteria score the
# shootout (knockout matches that went to penalties only).
REGULATION_CRITERIA = ("exact", "diff", "result")
PENALTY_CRITERIA = ("penalty_winner", "penalty_score", "penalty_diff")
CRITERIA = REGULATION_CRITERIA + PENALTY_CRITERIA

# Outcome labels (mutually exclusive — best tier the user achieved). The three
# penalty criteria collapse to one PENALTY tier for the headline badge; the
# per-criterion payouts are still tracked separately in PoolStats.
OUTCOME_EXACT = "exact"
OUTCOME_DIFF = "diff"
OUTCOME_RESULT = "result"
OUTCOME_PENALTY = "penalty"
OUTCOME_MISS = "miss"
OUTCOME_NO_PREDICTION = "no_prediction"
OUTCOME_NO_RESULT = "no_result"


@dataclass(frozen=True)
class StagePools:
    """Pool sizes for one stage (= one bracket slot). All integers (from admin)."""
    pool_exact: int
    pool_diff: int
    pool_result: int
    pool_penalty_winner: int
    pool_penalty_score: int
    pool_penalty_diff: int


@dataclass(frozen=True)
class Result:
    """Actual outcome of one match (one slot)."""
    home_team: str   # FIFA 3-letter code
    away_team: str
    home_score: int
    away_score: int
    went_to_penalties: bool = False
    penalty_winner: Optional[str] = None  # team code (3 letters)
    home_penalties: Optional[int] = None  # shootout score, when went_to_penalties
    away_penalties: Optional[int] = None


@dataclass(frozen=True)
class Prediction:
    """One user's prediction for one match in one prediction round."""
    round_order: int
    round_weight: Decimal
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    penalty_winner: Optional[str] = None  # only when draw on KO match
    home_penalties: Optional[int] = None  # shootout score, draw-on-KO predictions only
    away_penalties: Optional[int] = None


@dataclass
class UserScore:
    """Per-user output for one match."""
    user_id: int
    effective_round_order: Optional[int]
    score_exact: Decimal
    score_diff: Decimal
    score_result: Decimal
    score_penalty: Decimal
    total: Decimal
    outcome: str
    # Effective round's criterion satisfaction map — for "az yanlış" tiebreaker.
    satisfied: dict = field(default_factory=dict)


@dataclass
class PoolStats:
    """Per-criterion pool snapshot for one match. Drives MatchPool table + UI."""
    criterion: str
    pool_size: int
    predictor_count: int
    winner_count: int
    base_payout: Optional[Decimal]  # None when pool burned
    breakdown: dict  # {prediction_value (str): count (int)}


# ---------- Criterion satisfaction helpers ----------


def _matchup_correct(p: Prediction, r: Result) -> bool:
    """Strict matchup — home and away teams must line up exactly."""
    return p.home_team == r.home_team and p.away_team == r.away_team


def satisfies_exact(p: Prediction, r: Result) -> bool:
    if not _matchup_correct(p, r):
        return False
    return p.home_score == r.home_score and p.away_score == r.away_score


def satisfies_diff(p: Prediction, r: Result) -> bool:
    if not _matchup_correct(p, r):
        return False
    return (p.home_score - p.away_score) == (r.home_score - r.away_score)


def satisfies_result(p: Prediction, r: Result) -> bool:
    if not _matchup_correct(p, r):
        return False
    def _outcome(h: int, a: int) -> int:
        if h > a:
            return 1
        if h < a:
            return -1
        return 0
    return _outcome(p.home_score, p.away_score) == _outcome(r.home_score, r.away_score)


def _predicted_winner(p: Prediction) -> Optional[str]:
    """The team this prediction has advancing. For a draw, the chosen penalty
    winner (may be None); otherwise the higher-scoring side."""
    if p.home_score > p.away_score:
        return p.home_team
    if p.away_score > p.home_score:
        return p.away_team
    return p.penalty_winner


def satisfies_penalty_winner(p: Prediction, r: Result) -> bool:
    """User correctly named the team that advanced via penalties.

    Only meaningful when the match actually went to penalties. For non-draw
    predictions the implied winner (via score) is checked. For draw predictions
    the user's `penalty_winner` is checked. Open to any prediction.
    """
    if not r.went_to_penalties or r.penalty_winner is None:
        return False
    if not _matchup_correct(p, r):
        return False
    predicted = _predicted_winner(p)
    return predicted is not None and predicted == r.penalty_winner


def _has_pen_scores(p: Prediction) -> bool:
    return p.home_penalties is not None and p.away_penalties is not None


def satisfies_penalty_score(p: Prediction, r: Result) -> bool:
    """User predicted the exact penalty shootout score.

    Only draw predictions carry a shootout score, so non-draw predictions can
    never satisfy this. Matchup must line up and the match must have gone to pens.
    """
    if not r.went_to_penalties or r.home_penalties is None or r.away_penalties is None:
        return False
    if not _matchup_correct(p, r) or not _has_pen_scores(p):
        return False
    return p.home_penalties == r.home_penalties and p.away_penalties == r.away_penalties


def satisfies_penalty_diff(p: Prediction, r: Result) -> bool:
    """User predicted the penalty shootout goal difference (signed home−away)."""
    if not r.went_to_penalties or r.home_penalties is None or r.away_penalties is None:
        return False
    if not _matchup_correct(p, r) or not _has_pen_scores(p):
        return False
    return (p.home_penalties - p.away_penalties) == (r.home_penalties - r.away_penalties)


def satisfies(p: Prediction, r: Result, criterion: str) -> bool:
    if criterion == "exact":
        return satisfies_exact(p, r)
    if criterion == "diff":
        return satisfies_diff(p, r)
    if criterion == "result":
        return satisfies_result(p, r)
    if criterion == "penalty_winner":
        return satisfies_penalty_winner(p, r)
    if criterion == "penalty_score":
        return satisfies_penalty_score(p, r)
    if criterion == "penalty_diff":
        return satisfies_penalty_diff(p, r)
    raise ValueError(f"Unknown criterion: {criterion}")


def best_outcome(sat_map: dict[str, bool]) -> str:
    """Highest tier the user achieved on this match. The three penalty criteria
    collapse to one PENALTY tier, below the regulation tiers."""
    if sat_map.get("exact"):
        return OUTCOME_EXACT
    if sat_map.get("diff"):
        return OUTCOME_DIFF
    if sat_map.get("result"):
        return OUTCOME_RESULT
    if any(sat_map.get(c) for c in PENALTY_CRITERIA):
        return OUTCOME_PENALTY
    return OUTCOME_MISS


# ---------- Breakdown key helpers (for MatchPool.breakdown JSON) ----------


def breakdown_key(criterion: str, p: Prediction, r: Result) -> Optional[str]:
    """Returns the per-prediction string key for the ganyan-tablosu breakdown.

    Returns None if this prediction doesn't have a sensible value for this
    criterion (e.g., penalty_winner without a determinable winner, or a
    penalty_score/diff key for a prediction that carries no shootout score).
    """
    if criterion == "exact":
        return f"{p.home_score}-{p.away_score}"
    if criterion == "diff":
        return str(p.home_score - p.away_score)
    if criterion == "result":
        if p.home_score > p.away_score:
            return "H"
        if p.home_score < p.away_score:
            return "A"
        return "D"
    if criterion == "penalty_winner":
        return _predicted_winner(p)  # team code; None on a draw with no chosen winner
    if criterion == "penalty_score":
        if not _has_pen_scores(p):
            return None
        return f"{p.home_penalties}-{p.away_penalties}"
    if criterion == "penalty_diff":
        if not _has_pen_scores(p):
            return None
        return str(p.home_penalties - p.away_penalties)
    raise ValueError(f"Unknown criterion: {criterion}")


# ---------- Core scorer ----------


def _pool_map(pools: StagePools) -> dict[str, int]:
    """Criterion → pool size, in CRITERIA order."""
    return {
        "exact": pools.pool_exact,
        "diff": pools.pool_diff,
        "result": pools.pool_result,
        "penalty_winner": pools.pool_penalty_winner,
        "penalty_score": pools.pool_penalty_score,
        "penalty_diff": pools.pool_penalty_diff,
    }


def _round_score_given_payouts(
    pred: Prediction,
    result: Result,
    payouts: dict[str, Decimal],
) -> tuple[Decimal, dict[str, bool]]:
    """Weighted total a single round-prediction would earn for one user."""
    sat = {c: satisfies(pred, result, c) for c in CRITERIA}
    raw = sum((payouts[c] for c, s in sat.items() if s), Decimal("0"))
    return raw * pred.round_weight, sat


def _pick_effective_round(
    preds: list[Prediction],
    result: Result,
    payouts: dict[str, Decimal],
) -> tuple[Prediction, Decimal, dict[str, bool]]:
    """Pick the round that maximizes weighted total.

    Tie-break: prefer a pick on the actual fixture, then the earliest round. The
    on-fixture preference only bites when every candidate scores 0 — a user who
    predicted THIS match but missed ties at 0 with any earlier off-fixture
    bracket pick. Without the preference the off-fixture pick would win the tie
    and become effective, which then drops the user from the match/result views
    and the ganyan tablosu N (they filter on the effective pick's matchup) even
    though they did predict this fixture. It never changes a user's points: an
    off-fixture pick scores 0, so the swap is only ever between two 0-point picks.
    """
    # Sort by round_order so equal totals fall to the earliest.
    sorted_preds = sorted(preds, key=lambda p: p.round_order)
    best_pred = sorted_preds[0]
    best_score, best_sat = _round_score_given_payouts(best_pred, result, payouts)
    best_on_fixture = _matchup_correct(best_pred, result)
    for p in sorted_preds[1:]:
        score, sat = _round_score_given_payouts(p, result, payouts)
        on_fixture = _matchup_correct(p, result)
        if (score, on_fixture) > (best_score, best_on_fixture):
            best_pred, best_score, best_sat, best_on_fixture = p, score, sat, on_fixture
    return best_pred, best_score, best_sat


def compute_slot(
    predictions_by_user: dict[int, list[Prediction]],
    result: Result,
    pools: StagePools,
) -> tuple[dict[int, UserScore], list[PoolStats]]:
    """Score one match for all users who predicted it.

    Returns (per-user UserScore, per-criterion PoolStats list).

    `predictions_by_user` may be empty — returns empty dicts.
    """
    if not predictions_by_user:
        return {}, _empty_pool_stats(pools, predictor_count=0)

    pool_by_criterion = _pool_map(pools)

    # ---- Pick each user's effective round (single deterministic pass). ----
    # The effective round maximizes the user's weighted total against the
    # NOMINAL pools (each criterion at its full size). That choice depends only
    # on the user's own predictions — not on how the crowd splits the pools — so
    # there is no circular dependency and no iteration: one pass, one unique
    # result. (Scoring then uses the diluted payouts below.)
    nominal_payouts: dict[str, Decimal] = {c: Decimal(pool_by_criterion[c]) for c in CRITERIA}
    effective: dict[int, tuple[Prediction, dict[str, bool]]] = {}
    for uid, preds in predictions_by_user.items():
        best_pred, _, best_sat = _pick_effective_round(preds, result, nominal_payouts)
        effective[uid] = (best_pred, best_sat)

    # ---- Diluted payouts from the fixed effective picks. ----
    # base_payout_c = pool_c / |W_c|, where W_c = users whose effective round
    # satisfies c. Pool burns (payout 0) when no effective pick satisfies c.
    winner_count = {c: 0 for c in CRITERIA}
    for _pred, sat in effective.values():
        for c in CRITERIA:
            if sat[c]:
                winner_count[c] += 1
    payouts = {
        c: (Decimal(pool_by_criterion[c]) / winner_count[c]) if winner_count[c] else Decimal("0")
        for c in CRITERIA
    }

    # ---- Build UserScore rows. ----
    user_scores: dict[int, UserScore] = {}
    for uid, (pred, sat) in effective.items():
        scores_per_c = {
            c: (payouts[c] * pred.round_weight) if sat[c] else Decimal("0")
            for c in CRITERIA
        }
        total = sum(scores_per_c.values(), Decimal("0"))
        outcome = best_outcome(sat) if total > 0 else OUTCOME_MISS
        # The three penalty criteria collapse to one score_penalty column.
        score_penalty = sum((scores_per_c[c] for c in PENALTY_CRITERIA), Decimal("0"))
        user_scores[uid] = UserScore(
            user_id=uid,
            effective_round_order=pred.round_order,
            score_exact=scores_per_c["exact"],
            score_diff=scores_per_c["diff"],
            score_result=scores_per_c["result"],
            score_penalty=score_penalty,
            total=total,
            outcome=outcome,
            satisfied=dict(sat),
        )

    # ---- Build PoolStats rows. ----
    # N counts only users whose effective pick is on the actual fixture — the
    # same picks the breakdown shows. A knockout slot collects predictions from
    # everyone's bracket, but a pick on a different matchup (e.g. USA-MEX for
    # what turned out to be BRA-ARG) didn't predict *this* match, so it neither
    # appears in the breakdown nor counts toward N.
    predictor_count = sum(
        1 for pred, _sat in effective.values() if _matchup_correct(pred, result)
    )
    # winner_count was already tallied above (drives both payouts and PoolStats).

    # Breakdown counts use each user's effective-round prediction (avoids
    # double-counting users who predicted differently across rounds), and
    # exclude wrong-matchup picks — consistent with N above.
    breakdowns = {c: {} for c in CRITERIA}
    for pred, _sat in effective.values():
        if not _matchup_correct(pred, result):
            continue
        for c in CRITERIA:
            key = breakdown_key(c, pred, result)
            if key is None:
                continue
            breakdowns[c][key] = breakdowns[c].get(key, 0) + 1

    stats: list[PoolStats] = []
    for c in CRITERIA:
        wc = winner_count[c]
        base = (Decimal(pool_by_criterion[c]) / wc) if wc else None
        stats.append(PoolStats(
            criterion=c,
            pool_size=pool_by_criterion[c],
            predictor_count=predictor_count,
            winner_count=wc,
            base_payout=base,
            breakdown=breakdowns[c],
        ))

    return user_scores, stats


def _empty_pool_stats(pools: StagePools, predictor_count: int) -> list[PoolStats]:
    """Pool stats with zero winners — used when no predictions exist."""
    pool_by_criterion = _pool_map(pools)
    return [
        PoolStats(
            criterion=c,
            pool_size=pool_by_criterion[c],
            predictor_count=predictor_count,
            winner_count=0,
            base_payout=None,
            breakdown={},
        )
        for c in CRITERIA
    ]


def compute_pre_result_pools(
    predictions_by_user: dict[int, list[Prediction]],
    pools: StagePools,
) -> list[PoolStats]:
    """Compute MatchPool breakdown counts BEFORE actual result is known.

    Used by the lock-time aggregation so the ganyan tablosu UI can show
    'X people predicted 1-0' before the match ends. Winner counts and
    base_payouts are not meaningful here (no result), so they're left at 0/None.

    Each user contributes their LATEST round's prediction (highest round_order).
    """
    pool_by_criterion = _pool_map(pools)
    breakdowns = {c: {} for c in CRITERIA}
    for preds in predictions_by_user.values():
        # Latest round = highest round_order.
        latest = max(preds, key=lambda p: p.round_order)
        # Pre-result we only show regulation breakdowns. Penalty criteria depend
        # on whether the match goes to pens (unknown pre-result), so skip them.
        for c in REGULATION_CRITERIA:
            key = breakdown_key(c, latest, _DUMMY_RESULT)
            if key is None:
                continue
            breakdowns[c][key] = breakdowns[c].get(key, 0) + 1

    return [
        PoolStats(
            criterion=c,
            pool_size=pool_by_criterion[c],
            predictor_count=len(predictions_by_user),
            winner_count=0,
            base_payout=None,
            breakdown=breakdowns[c],
        )
        for c in CRITERIA
    ]


# Dummy result for breakdown_key — only the home/away team codes need to
# "exist", and we only call it with criteria that don't read result fields.
_DUMMY_RESULT = Result(home_team="", away_team="", home_score=0, away_score=0)


# ---------- Pre-result "best case" payout ----------


def _self_result(home_team: str, away_team: str, pred: Prediction) -> Result:
    """The match's Result if it ended exactly as `pred` calls it.

    Built on the actual teams (so a wrong-matchup prediction satisfies nothing);
    a draw that carries shootout data is treated as having gone to penalties.
    """
    is_draw = pred.home_score == pred.away_score
    has_shootout = pred.penalty_winner is not None or (
        pred.home_penalties is not None and pred.away_penalties is not None
    )
    went_to_penalties = is_draw and has_shootout
    return Result(
        home_team=home_team,
        away_team=away_team,
        home_score=pred.home_score,
        away_score=pred.away_score,
        went_to_penalties=went_to_penalties,
        penalty_winner=pred.penalty_winner if went_to_penalties else None,
        home_penalties=pred.home_penalties if went_to_penalties else None,
        away_penalties=pred.away_penalties if went_to_penalties else None,
    )


def potential_max_scores(
    pred_by_user: dict[int, Prediction],
    pools: StagePools,
    home_team: str,
    away_team: str,
) -> dict[int, Decimal]:
    """Best-case ganyan payout each user could still earn on a not-yet-scored
    match, assuming it ends EXACTLY as they predicted (shootout included for a
    draw-on-KO prediction).

    Same parimutuel arithmetic as `compute_slot`, evaluated against each user's
    own predicted outcome instead of a real result: under that hypothetical,
    every criterion the user's prediction satisfies pays ``pool_c / |winners_c|``
    (winners = users whose own prediction also satisfies c against it), scaled by
    the user's round weight.

    `home_team`/`away_team` are the match's actual team codes — known once both
    sides are set. Predictions on a different matchup can never score this slot,
    so they're dropped (absent from the returned map). Values are upper bounds in
    the common single-round case; the live engine can only pay *more* — and only
    when a co-winner's effective round turns out to sit elsewhere.
    """
    pool_by_criterion = _pool_map(pools)
    # Only predictions on the actual fixture are reachable; the rest score nothing.
    live = {
        uid: p for uid, p in pred_by_user.items()
        if p.home_team == home_team and p.away_team == away_team
    }
    out: dict[int, Decimal] = {}
    for uid, pred in live.items():
        hypothetical = _self_result(home_team, away_team, pred)
        total = Decimal("0")
        for c in CRITERIA:
            if not satisfies(pred, hypothetical, c):
                continue
            winners = sum(1 for q in live.values() if satisfies(q, hypothetical, c))
            total += (Decimal(pool_by_criterion[c]) / winners) * pred.round_weight
        out[uid] = total
    return out


@dataclass(frozen=True)
class BestCase:
    """Pre-result best case for one pick, split by scenario.

    ``regulation`` is the payout if the match ends exactly on the predicted
    scoreline, counting the regulation (120') pools only. ``with_penalties``
    is the best case once the shootout pools are in reach: for a draw pick
    carrying a shootout it's the full six-pool self-scenario; for a decisive
    pick it's the better of the regulation scenario and the goes-to-penalties
    scenario (mutually exclusive — a level match pays only the implied-winner
    pool to a decisive pick). Equal values mean penalties add nothing.
    """
    regulation: Decimal
    with_penalties: Decimal


def potential_max_scores_multi(
    preds_by_user: dict[int, list[Prediction]],
    pools: StagePools,
    home_team: str,
    away_team: str,
    knockout: bool = True,
) -> dict[int, list[BestCase]]:
    """Per-prediction best case, for users who may carry one pick per round.

    Like `potential_max_scores`, but it keeps every round a user predicted
    instead of collapsing to one — the all-predictions card lists each pick on
    its own row with its round weight, so each row needs its own "en fazla" —
    and it splits each best case into a `BestCase`: the 120'-scoreline part vs
    the best achievable once penalty pools count. The split matters both ways:
    a draw-on-KO pick's shootout winnings aren't a sure part of its scoreline
    hitting, and a decisive pick can out-earn its own scenario when the match
    goes to penalties and its implied winner advances (the shown maximum must
    cover that path too).

    Co-winner denominators count distinct USERS, not predictions: a user with
    two picks that both satisfy a criterion still dilutes that pool by one — the
    live engine only ever scores one round per user, so the same user can't take
    two slices of the same pool.

    Returns ``{user_id: [BestCase aligned to the input list]}``. A pick on a
    different matchup can never score this fixture and yields zeros; pass lists
    already filtered to the real fixture for a clean one-value-per-real-pick map.

    ``knockout=False`` (group match) disables the penalties scenario entirely —
    group matches can't go to a shootout, even though the stage config carries
    (always-burning) penalty pools.
    """
    pool_by_criterion = _pool_map(pools)

    def _user_wins(criterion: str, hypothetical: Result) -> int:
        """Distinct users holding at least one fixture pick that satisfies it."""
        return sum(
            1 for preds in preds_by_user.values()
            if any(
                p.home_team == home_team and p.away_team == away_team
                and satisfies(p, hypothetical, criterion)
                for p in preds
            )
        )

    out: dict[int, list[BestCase]] = {}
    for uid, preds in preds_by_user.items():
        row_scores: list[BestCase] = []
        for pred in preds:
            if pred.home_team != home_team or pred.away_team != away_team:
                row_scores.append(BestCase(Decimal("0"), Decimal("0")))
                continue
            hypothetical = _self_result(home_team, away_team, pred)
            regulation = Decimal("0")
            pens_self = Decimal("0")
            for c in CRITERIA:
                if not satisfies(pred, hypothetical, c):
                    continue
                # winners >= 1: this pick's own user always counts.
                winners = _user_wins(c, hypothetical)
                slice_ = (Decimal(pool_by_criterion[c]) / winners) * pred.round_weight
                if c in REGULATION_CRITERIA:
                    regulation += slice_
                else:
                    pens_self += slice_

            if hypothetical.went_to_penalties:
                # Draw pick with a shootout: its self-scenario already IS the
                # penalties scenario.
                with_pens = regulation + pens_self
            else:
                # Decisive (or shootout-less draw) pick: the alternative
                # scenario is the match going to penalties with this pick's
                # implied winner advancing — it loses every regulation pool
                # (the effective score is level) but wins the winner pool.
                winner = _predicted_winner(pred) if knockout else None
                with_pens = regulation
                if winner is not None:
                    pens_hypo = Result(
                        home_team=home_team, away_team=away_team,
                        home_score=0, away_score=0,
                        went_to_penalties=True, penalty_winner=winner,
                    )
                    winners = _user_wins("penalty_winner", pens_hypo)
                    pw_slice = (
                        Decimal(pool_by_criterion["penalty_winner"]) / winners
                    ) * pred.round_weight
                    with_pens = max(regulation, pw_slice)
            row_scores.append(BestCase(regulation, with_pens))
        out[uid] = row_scores
    return out
