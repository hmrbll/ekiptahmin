"""Minimal DB fixtures for liveresults mapping/sync tests."""

from datetime import datetime, timezone as dt_tz

import pytest

from apps.tournament.models import BracketSlot, Stage, Team, Tournament


@pytest.fixture
def tournament(db):
    return Tournament.objects.create(
        name="Test WC", slug="test-wc",
        start_date="2026-06-11", end_date="2026-07-19", is_active=True,
    )


@pytest.fixture
def group_stage(tournament):
    return Stage.objects.create(
        tournament=tournament, kind=Stage.GROUP, order=0,
        points_exact=6, points_diff=4, points_result=2,
    )


@pytest.fixture
def teams(tournament):
    tur = Team.objects.create(tournament=tournament, code="TUR", name_tr="Türkiye", group_letter="A")
    bra = Team.objects.create(tournament=tournament, code="BRA", name_tr="Brezilya", group_letter="A")
    return {"TUR": tur, "BRA": bra}


@pytest.fixture
def slot(tournament, group_stage, teams):
    return BracketSlot.objects.create(
        tournament=tournament, stage=group_stage, position="GroupA-M1",
        scheduled_kickoff=datetime(2026, 6, 11, 19, 0, tzinfo=dt_tz.utc),
        home_team_actual=teams["TUR"], away_team_actual=teams["BRA"],
    )
