"""Pure-Python scoring engine for the ekiptahmin.com prediction game.

Operates on plain dataclasses — no Django/ORM dependency. Exists in apps/scoring/
for namespacing only; can be imported and used from anywhere (Phase 4 prediction
flow, Phase 6 leaderboard rebuilds, Phase 8 simulation).

The mechanic (inherited from the 2022 World Cup Excel game):

For each (user, slot) the engine looks at all of the user's predictions for that
slot across every prediction round. It locates the EARLIEST round in which the
user predicted the matchup correctly (same home + same away team), then scores
that prediction against the actual result and applies the round's weight
multiplier. Predictions made in later rounds are ignored once an earlier-round
match has been found — early correct calls are rewarded.

Special cases:
- "Penalty loser bonus": if the user predicted a non-draw on a correct matchup
  but the match went to penalties, and the team they picked is the one that
  advanced via penalties, they receive `ROUND(points_result * penalty_loser_pct
  * weight)` rather than zero.
- "Penalty shootout bonus": if the user predicted a draw on a correct matchup
  and the match went to penalties, regular score is awarded for the 90' draw,
  PLUS a bonus is added based on penalty prediction quality (currently scored
  using the group-stage points table; refine when 2022 rule is confirmed).

Strict matchup: home/away order must match exactly. (Hemre's call — the 2022
rule treated reversed orientation as a wrong matchup.)
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Optional

_ONE = Decimal("1")  # for `quantize` to nearest integer


# ---------- Configuration types ----------


@dataclass(frozen=True)
class StageConfig:
    """Scoring config for a single tournament stage (Group, R32, ..., Final)."""

    points_exact: int
    points_diff: int
    points_result: int
    penalty_loser_pct: Decimal


@dataclass(frozen=True)
class RoundConfig:
    """Defines a prediction round's identity and weight."""

    order: int
    weight: Decimal


# ---------- Domain types ----------


@dataclass(frozen=True)
class Result:
    """The actual outcome of a slot."""

    home_team: str   # FIFA 3-letter code
    away_team: str
    home_score: int
    away_score: int
    went_to_penalties: bool = False
    penalty_winner: Optional[str] = None
    home_penalties: Optional[int] = None
    away_penalties: Optional[int] = None


@dataclass(frozen=True)
class Prediction:
    """One prediction for one slot, made by a user in a specific round."""

    round: RoundConfig
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    # Penalty fields are only meaningful when the user predicted a draw at 90'.
    penalty_winner: Optional[str] = None
    home_penalties: Optional[int] = None
    away_penalties: Optional[int] = None


# ---------- Output type ----------

MATCHUP_TYPES = (
    "exact",                  # predicted score == actual score
    "diff",                   # correct outcome + correct goal difference
    "result",                 # correct outcome only
    "penalty_loser_bonus",    # predicted non-draw, named correct penalty winner
    "miss",                   # predicted matchup correctly but scored zero
    "no_prediction",          # never predicted this slot
)


@dataclass(frozen=True)
class ScoreBreakdown:
    matchup_type: str
    points_match: Decimal      # final match points (already weighted; rounded for loser bonus)
    points_penalty: Decimal    # penalty shootout bonus (already weighted)
    total: Decimal             # points_match + points_penalty
    earning_round_order: Optional[int]


_ZERO_BREAKDOWN_NO_PRED = ScoreBreakdown(
    matchup_type="no_prediction",
    points_match=Decimal("0"),
    points_penalty=Decimal("0"),
    total=Decimal("0"),
    earning_round_order=None,
)
_ZERO_BREAKDOWN_MISS = ScoreBreakdown(
    matchup_type="miss",
    points_match=Decimal("0"),
    points_penalty=Decimal("0"),
    total=Decimal("0"),
    earning_round_order=None,
)


# ---------- Internals ----------


def _outcome(home_score: int, away_score: int) -> int:
    """1 = home win, -1 = away win, 0 = draw."""
    if home_score > away_score:
        return 1
    if home_score < away_score:
        return -1
    return 0


def _matchup_correct(p: Prediction, actual: Result) -> bool:
    """Strict: home and away teams must match exactly (no orientation swap)."""
    return p.home_team == actual.home_team and p.away_team == actual.away_team


def _classify_correct_matchup(
    p: Prediction, actual: Result, stage: StageConfig
) -> tuple[str, Decimal]:
    """Score `p` vs `actual` assuming the matchup is already correct.
    Returns (matchup_type, weighted_points).
    NOTE: weight is NOT applied here — caller multiplies. Returns the integer
    base value as a Decimal for consistency. The penalty-loser branch returns
    `points_result * penalty_loser_pct` UNROUNDED — caller is responsible for
    multiplying by weight and rounding.
    """
    if p.home_score == actual.home_score and p.away_score == actual.away_score:
        return ("exact", Decimal(stage.points_exact))

    pred_outcome = _outcome(p.home_score, p.away_score)
    actual_outcome = _outcome(actual.home_score, actual.away_score)

    if pred_outcome == actual_outcome:
        # Same winner (or both predicted/actual draw). Check goal difference.
        pred_diff = p.home_score - p.away_score
        actual_diff = actual.home_score - actual.away_score
        if pred_diff == actual_diff:
            return ("diff", Decimal(stage.points_diff))
        return ("result", Decimal(stage.points_result))

    # Outcome mismatch — check the penalty-loser bonus special case.
    if (
        actual.went_to_penalties
        and actual.penalty_winner is not None
        and pred_outcome != 0  # user predicted a winner (not a draw)
    ):
        pred_winner = p.home_team if pred_outcome == 1 else p.away_team
        if pred_winner == actual.penalty_winner:
            # UNROUNDED base; rounding happens after weight is applied.
            base_unrounded = Decimal(stage.points_result) * stage.penalty_loser_pct
            return ("penalty_loser_bonus", base_unrounded)

    return ("miss", Decimal("0"))


def _penalty_shootout_bonus_base(
    p: Prediction, actual: Result, group_stage: StageConfig
) -> int:
    """If the user predicted a draw on a correct matchup AND the match went to
    penalties AND they correctly named the penalty winner, return a bonus base
    (int, before weight) using group_stage scoring on their penalty score
    prediction. Returns 0 otherwise.

    NOTE: the exact 2022 rule is TBD per Hemre — this is a working interpretation
    that scores the penalty result as if it were a separate group-stage match.
    """
    if not actual.went_to_penalties or actual.penalty_winner is None:
        return 0
    if _outcome(p.home_score, p.away_score) != 0:
        return 0  # user did not predict a draw
    if p.penalty_winner is None or p.penalty_winner != actual.penalty_winner:
        return 0  # wrong (or missing) penalty winner

    # Correct winner. Score the penalty score itself.
    have_pred_score = p.home_penalties is not None and p.away_penalties is not None
    have_actual_score = actual.home_penalties is not None and actual.away_penalties is not None
    if not (have_pred_score and have_actual_score):
        # Winner correct but no score detail — award only the result tier.
        return group_stage.points_result

    if (
        p.home_penalties == actual.home_penalties
        and p.away_penalties == actual.away_penalties
    ):
        return group_stage.points_exact
    if (p.home_penalties - p.away_penalties) == (
        actual.home_penalties - actual.away_penalties
    ):
        return group_stage.points_diff
    return group_stage.points_result


# ---------- Public API ----------


def score_slot(
    user_predictions: Iterable[Prediction],
    actual: Result,
    stage: StageConfig,
    group_stage: StageConfig,
) -> ScoreBreakdown:
    """Compute the score for one user on one slot.

    `user_predictions` may be empty (returns "no_prediction" breakdown). When
    multiple predictions exist for the same round.order, the iteration order is
    preserved among those — callers should pass at most one prediction per round
    (the latest one if there were edits).
    """
    sorted_preds = sorted(user_predictions, key=lambda p: p.round.order)
    if not sorted_preds:
        return _ZERO_BREAKDOWN_NO_PRED

    earning_pred: Optional[Prediction] = None
    matchup_type = "miss"
    base_unrounded = Decimal("0")

    for pred in sorted_preds:
        if _matchup_correct(pred, actual):
            matchup_type, base_unrounded = _classify_correct_matchup(pred, actual, stage)
            earning_pred = pred
            break

    if earning_pred is None:
        return _ZERO_BREAKDOWN_MISS

    weight = earning_pred.round.weight

    if matchup_type == "penalty_loser_bonus":
        # Spec: ROUND(points_result × penalty_loser_pct × weight) → integer
        points_match = (base_unrounded * weight).quantize(_ONE, rounding=ROUND_HALF_UP)
    else:
        points_match = base_unrounded * weight

    # Penalty shootout bonus (only when user predicted a draw and got points for it)
    points_penalty = Decimal("0")
    if matchup_type in {"exact", "diff", "result"}:
        bonus_base = _penalty_shootout_bonus_base(earning_pred, actual, group_stage)
        if bonus_base > 0:
            points_penalty = Decimal(bonus_base) * weight

    return ScoreBreakdown(
        matchup_type=matchup_type,
        points_match=points_match,
        points_penalty=points_penalty,
        total=points_match + points_penalty,
        earning_round_order=earning_pred.round.order,
    )
