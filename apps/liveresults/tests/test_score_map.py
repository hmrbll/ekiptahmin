"""Pure tests for map_score() — the football-data score → ActualResult mapping.

No DB. These pin the 90'-regulation semantics that drive everyone's scores, so
a provider quirk (extra-time goals in fullTime, missing regularTime, ...) can't
silently corrupt results.
"""

import pytest

from apps.liveresults.score import ScoreMappingError, map_score


def test_none_or_empty_score_returns_none():
    assert map_score(None) is None
    assert map_score({}) is None


def test_scheduled_match_no_score_yet():
    score = {"duration": "REGULAR", "fullTime": {"home": None, "away": None}}
    assert map_score(score) is None


def test_regular_full_time():
    score = {"winner": "HOME_TEAM", "duration": "REGULAR",
             "fullTime": {"home": 2, "away": 0}, "halfTime": {"home": 1, "away": 0}}
    out = map_score(score)
    assert out["home_score"] == 2 and out["away_score"] == 0
    assert out["went_to_extra_time"] is False
    assert out["went_to_penalties"] is False
    assert out["penalty_winner_side"] is None
    assert out["home_score_aet"] is None and out["away_score_aet"] is None


def test_in_play_uses_running_full_time():
    # Live regular play: duration REGULAR, fullTime is the running score.
    score = {"duration": "REGULAR", "fullTime": {"home": 1, "away": 0}}
    out = map_score(score)
    assert (out["home_score"], out["away_score"]) == (1, 0)


def test_extra_time_uses_regular_time_for_90():
    score = {"winner": "AWAY_TEAM", "duration": "EXTRA_TIME",
             "fullTime": {"home": 1, "away": 2},      # incl. ET goal
             "regularTime": {"home": 1, "away": 1},   # the 90' score (a draw)
             "extraTime": {"home": 0, "away": 1}}
    out = map_score(score)
    assert (out["home_score"], out["away_score"]) == (1, 1)  # 90', not 1-2
    assert out["went_to_extra_time"] is True
    assert out["went_to_penalties"] is False


def test_penalty_winner_derived_from_shootout_score_not_winner_field():
    # `winner` deliberately contradicts the shootout score — we trust the score.
    score = {"winner": "AWAY_TEAM", "duration": "PENALTY_SHOOTOUT",
             "fullTime": {"home": 1, "away": 1},
             "regularTime": {"home": 1, "away": 1},
             "penalties": {"home": 4, "away": 2}}
    out = map_score(score)
    assert (out["home_score"], out["away_score"]) == (1, 1)
    assert out["went_to_extra_time"] is True
    assert out["went_to_penalties"] is True
    assert (out["home_penalties"], out["away_penalties"]) == (4, 2)
    assert out["penalty_winner_side"] == "HOME"  # 4 > 2, ignoring the winner field


def test_penalty_away_wins_shootout():
    score = {"duration": "PENALTY_SHOOTOUT",
             "fullTime": {"home": 0, "away": 0},
             "regularTime": {"home": 0, "away": 0},
             "penalties": {"home": 3, "away": 5}}
    out = map_score(score)
    assert out["penalty_winner_side"] == "AWAY"


def test_penalty_tied_shootout_has_no_winner_yet():
    # Live shootout, currently level → went_to_penalties but no winner yet.
    score = {"duration": "PENALTY_SHOOTOUT",
             "fullTime": {"home": 1, "away": 1},
             "regularTime": {"home": 1, "away": 1},
             "penalties": {"home": 2, "away": 2}}
    out = map_score(score)
    assert out["went_to_penalties"] is True
    assert out["penalty_winner_side"] is None


def test_penalty_non_draw_90_raises():
    score = {"duration": "PENALTY_SHOOTOUT",
             "fullTime": {"home": 2, "away": 1},
             "regularTime": {"home": 2, "away": 1},
             "penalties": {"home": 4, "away": 2}}
    with pytest.raises(ScoreMappingError):
        map_score(score)


def test_extra_time_missing_regular_time_falls_back_to_full_time():
    # Defensive: a live ET payload before regularTime is populated.
    score = {"winner": None, "duration": "EXTRA_TIME", "fullTime": {"home": 1, "away": 1}}
    out = map_score(score)
    assert (out["home_score"], out["away_score"]) == (1, 1)
    assert out["went_to_extra_time"] is True
