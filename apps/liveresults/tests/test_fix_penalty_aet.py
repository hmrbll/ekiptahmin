"""Tests for the fix_penalty_aet repair command.

Pins the one-off repair of penalty rows whose 120' score was stored
penalty-inflated (fullTime, which folds in the shootout goals) before the
map_score fix. See apps/liveresults/score.py + docs/live-results.md.
"""

from datetime import datetime, timezone as dt_tz

import pytest
from django.core.management import call_command

from apps.tournament.models import ActualResult, BracketSlot, Stage


@pytest.fixture
def ko_stage(tournament):
    return Stage.objects.create(
        tournament=tournament, kind=Stage.R32, order=1,
        points_exact=6, points_diff=4, points_result=2,
    )


def _penalty_slot(tournament, ko_stage, teams, position, *, aet, pens, winner):
    slot = BracketSlot.objects.create(
        tournament=tournament, stage=ko_stage, position=position,
        scheduled_kickoff=datetime(2026, 6, 29, 19, 0, tzinfo=dt_tz.utc),
        home_team_actual=teams["TUR"], away_team_actual=teams["BRA"],
    )
    ActualResult.objects.create(
        slot=slot, home_score=1, away_score=1,  # 90' draw
        went_to_extra_time=True, went_to_penalties=True,
        home_score_aet=aet[0], away_score_aet=aet[1],
        home_penalties=pens[0], away_penalties=pens[1],
        penalty_winner=winner, source="API",
    )
    return slot


@pytest.mark.django_db
def test_strips_penalties_from_inflated_120_score(tournament, ko_stage, teams):
    # 1-1 ET draw won 3-4 on penalties was stored as fullTime 4-5.
    slot = _penalty_slot(tournament, ko_stage, teams, "R32-2",
                         aet=(4, 5), pens=(3, 4), winner=teams["BRA"])

    call_command("fix_penalty_aet")

    r = ActualResult.objects.get(slot=slot)
    assert (r.home_score_aet, r.away_score_aet) == (1, 1)
    assert (r.effective_home_score, r.effective_away_score) == (1, 1)
    # 90' score + shootout untouched.
    assert (r.home_score, r.away_score) == (1, 1)
    assert (r.home_penalties, r.away_penalties) == (3, 4)


@pytest.mark.django_db
def test_repairs_tied_shootout_row_and_flags_it(tournament, ko_stage, teams, capsys):
    # Degenerate row: shootout captured tied (3-3, no winner) → aet 4-4.
    slot = _penalty_slot(tournament, ko_stage, teams, "R32-3",
                         aet=(4, 4), pens=(3, 3), winner=None)

    call_command("fix_penalty_aet")

    r = ActualResult.objects.get(slot=slot)
    assert (r.home_score_aet, r.away_score_aet) == (1, 1)
    assert "no penalty winner" in capsys.readouterr().out


@pytest.mark.django_db
def test_is_idempotent(tournament, ko_stage, teams):
    slot = _penalty_slot(tournament, ko_stage, teams, "R32-2",
                         aet=(4, 5), pens=(3, 4), winner=teams["BRA"])

    call_command("fix_penalty_aet")
    call_command("fix_penalty_aet")  # second run must not touch the clean row

    r = ActualResult.objects.get(slot=slot)
    assert (r.home_score_aet, r.away_score_aet) == (1, 1)


@pytest.mark.django_db
def test_dry_run_writes_nothing(tournament, ko_stage, teams):
    slot = _penalty_slot(tournament, ko_stage, teams, "R32-2",
                         aet=(4, 5), pens=(3, 4), winner=teams["BRA"])

    call_command("fix_penalty_aet", "--dry-run")

    r = ActualResult.objects.get(slot=slot)
    assert (r.home_score_aet, r.away_score_aet) == (4, 5)  # untouched


@pytest.mark.django_db
def test_leaves_already_clean_row_untouched(tournament, ko_stage, teams):
    # A row already storing the clean 120' draw must not be re-stripped.
    slot = _penalty_slot(tournament, ko_stage, teams, "R32-4",
                         aet=(2, 2), pens=(5, 4), winner=teams["TUR"])

    call_command("fix_penalty_aet")

    r = ActualResult.objects.get(slot=slot)
    assert (r.home_score_aet, r.away_score_aet) == (2, 2)
