"""Translate a football-data `score` object into our ActualResult fields.

Pure, DB-free, so it's trivially unit-testable. The sync layer resolves
`penalty_winner_side` to an actual Team and persists the rest.

Our canonical score is the **90-minute regulation** result. football-data's
`score` object (v4) — confirmed against the live API + docs/overtime.html:

    REGULAR           fullTime = the 90' score (also the running score in-play)
    EXTRA_TIME        fullTime = score through 120'; regularTime = the 90' score
    PENALTY_SHOOTOUT  fullTime = score after 120'; regularTime = 90' score;
                      penalties = shootout-only goals; winner = shootout winner

So for any beyond-90' match we take `regularTime` (a draw, by definition) for
home/away_score and flag extra time / penalties separately — matching the
manual entry form's invariants (a penalty match is a 90' draw).
"""

from __future__ import annotations

DURATION_REGULAR = "REGULAR"
DURATION_EXTRA_TIME = "EXTRA_TIME"
DURATION_PENALTY = "PENALTY_SHOOTOUT"


class ScoreMappingError(ValueError):
    """The payload can't be mapped to a valid 90' ActualResult (skip + log it)."""


def _pair(node: dict | None) -> tuple[int | None, int | None]:
    node = node or {}
    return node.get("home"), node.get("away")


def map_score(score: dict | None) -> dict | None:
    """Map a football-data `score` object to ActualResult field values.

    Returns a dict:
        {home_score, away_score, went_to_extra_time, went_to_penalties,
         home_penalties, away_penalties, penalty_winner_side}
    where `penalty_winner_side` is "HOME" / "AWAY" / None (the sync resolves it
    to a Team). Returns None when there is no usable score yet (match not
    started / no goals recorded). Raises ScoreMappingError on malformed data.
    """
    if not score:
        return None

    duration = score.get("duration") or DURATION_REGULAR
    full_h, full_a = _pair(score.get("fullTime"))

    # No score recorded yet (SCHEDULED / TIMED) → nothing to write.
    if full_h is None or full_a is None:
        return None

    base = {
        "went_to_extra_time": False,
        "went_to_penalties": False,
        "home_penalties": None,
        "away_penalties": None,
        "penalty_winner_side": None,
        # 120' score; set only for beyond-90' matches (see ActualResult).
        "home_score_aet": None,
        "away_score_aet": None,
    }

    if duration == DURATION_REGULAR:
        # 90' (or live running) score is fullTime.
        return {**base, "home_score": full_h, "away_score": full_a}

    # Beyond 90' → the canonical 90' score lives in regularTime; fullTime is the
    # 120' (after-extra-time) score, which the bracket resolver uses to pick the
    # ET winner.
    reg_h, reg_a = _pair(score.get("regularTime"))
    if reg_h is None or reg_a is None:
        # regularTime should be present once duration leaves REGULAR. If a live
        # ET match hasn't populated it yet, fall back to fullTime so we still
        # show *something*; the next poll corrects it.
        reg_h, reg_a = full_h, full_a
    base = {**base, "home_score_aet": full_h, "away_score_aet": full_a}

    if duration == DURATION_EXTRA_TIME:
        return {**base, "home_score": reg_h, "away_score": reg_a, "went_to_extra_time": True}

    if duration == DURATION_PENALTY:
        if reg_h != reg_a:
            raise ScoreMappingError(
                f"Penalty shootout with a non-draw 90' score ({reg_h}-{reg_a}); "
                "cannot map (our model requires a 90' draw)."
            )
        pen_h, pen_a = _pair(score.get("penalties"))
        # Winner derives from the shootout score (more goals advances), NOT the
        # provider's `winner` field. None while tied / not yet recorded (a live
        # shootout mid-round) — the scoring engine simply withholds the
        # penalty-winner pool until a leader exists.
        side = None
        if pen_h is not None and pen_a is not None and pen_h != pen_a:
            side = "HOME" if pen_h > pen_a else "AWAY"
        return {
            **base,
            "home_score": reg_h,
            "away_score": reg_a,
            "went_to_extra_time": True,
            "went_to_penalties": True,
            "home_penalties": pen_h,
            "away_penalties": pen_a,
            "penalty_winner_side": side,
        }

    # Unknown duration → treat as regular running score, log via caller.
    return {**base, "home_score": full_h, "away_score": full_a}
