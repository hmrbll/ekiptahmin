"""Pin the rule: knockout exact/diff/result criteria judge the 120' score when
the match went to extra time, else the 90' score. Penalty criteria untouched.

`effective_*_score` (pure property) is the single source of truth; the ganyan
bridge feeds it into the engine's Result.
"""

import pytest

from apps.scoring.ganyan_bridge import _build_result
from apps.tournament.models import ActualResult, BracketSlot, Stage, Team, Tournament


# ---------- pure property (no DB) ----------


def test_effective_score_regular_is_90():
    ar = ActualResult(home_score=2, away_score=0)
    assert (ar.effective_home_score, ar.effective_away_score) == (2, 0)


def test_effective_score_extra_time_is_120():
    ar = ActualResult(home_score=1, away_score=1, went_to_extra_time=True,
                      home_score_aet=2, away_score_aet=1)
    assert (ar.effective_home_score, ar.effective_away_score) == (2, 1)


def test_effective_score_falls_back_to_90_when_aet_missing():
    # Manual ET entry that didn't capture the 120' score → degrade to 90'.
    ar = ActualResult(home_score=1, away_score=1, went_to_extra_time=True)
    assert (ar.effective_home_score, ar.effective_away_score) == (1, 1)


# ---------- bridge integration (DB) ----------


@pytest.fixture
def qf_slot(db):
    t = Tournament.objects.create(
        name="T", slug="t", start_date="2026-06-11", end_date="2026-07-19", is_active=True,
    )
    stage = Stage.objects.create(
        tournament=t, kind=Stage.QF, order=3, points_exact=6, points_diff=4, points_result=2,
    )
    home = Team.objects.create(tournament=t, code="TUR", name_tr="Türkiye")
    away = Team.objects.create(tournament=t, code="BRA", name_tr="Brezilya")
    return BracketSlot.objects.create(
        tournament=t, stage=stage, position="QF-1",
        scheduled_kickoff="2026-07-04T19:00:00Z",
        home_team_actual=home, away_team_actual=away,
    )


def test_bridge_uses_aet_score_for_extra_time(qf_slot):
    ar = ActualResult.objects.create(
        slot=qf_slot, home_score=1, away_score=1,
        went_to_extra_time=True, home_score_aet=2, away_score_aet=1,
    )
    result = _build_result(ar)
    assert (result.home_score, result.away_score) == (2, 1)  # 120', not the 90' draw


def test_bridge_penalty_keeps_draw_and_winner(qf_slot):
    ar = ActualResult.objects.create(
        slot=qf_slot, home_score=1, away_score=1,
        went_to_extra_time=True, went_to_penalties=True,
        home_penalties=5, away_penalties=4, home_score_aet=1, away_score_aet=1,
        penalty_winner=qf_slot.home_team_actual,
    )
    result = _build_result(ar)
    assert (result.home_score, result.away_score) == (1, 1)
    assert result.went_to_penalties is True
    assert result.penalty_winner == "TUR"
