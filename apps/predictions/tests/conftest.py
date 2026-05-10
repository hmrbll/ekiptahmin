"""Shared fixtures for predictions tests.

Builds a minimal in-memory tournament: 2 stages (Group + R16), 1 prediction
round that allows editing both stages, 4 teams, and 2 slots (one group, one
knockout). Tests can extend by creating extra rows on top.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.tournament.models import (
    BracketSlot,
    PredictionRound,
    Stage,
    Team,
    Tournament,
)


@pytest.fixture
def tournament(db):
    return Tournament.objects.create(
        name="Test Cup",
        slug="test-cup",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 7, 1),
        is_active=True,
    )


@pytest.fixture
def stage_group(tournament):
    return Stage.objects.create(
        tournament=tournament, kind=Stage.GROUP, order=0,
        points_exact=6, points_diff=4, points_result=2,
        penalty_loser_pct=Decimal("0.60"),
    )


@pytest.fixture
def stage_r16(tournament):
    return Stage.objects.create(
        tournament=tournament, kind=Stage.R16, order=2,
        points_exact=9, points_diff=6, points_result=3,
        penalty_loser_pct=Decimal("0.60"),
    )


@pytest.fixture
def prediction_round(tournament, stage_group, stage_r16):
    pr = PredictionRound.objects.create(
        tournament=tournament,
        name="Pre-tournament",
        order=0,
        deadline=timezone.now() + timedelta(days=10),
        weight=Decimal("1.00"),
    )
    pr.editable_stages.set([stage_group, stage_r16])
    return pr


@pytest.fixture
def team_tur(tournament):
    return Team.objects.create(tournament=tournament, code="TUR", name_tr="Türkiye", group_letter="A")


@pytest.fixture
def team_bra(tournament):
    return Team.objects.create(tournament=tournament, code="BRA", name_tr="Brezilya", group_letter="A")


@pytest.fixture
def team_arg(tournament):
    return Team.objects.create(tournament=tournament, code="ARG", name_tr="Arjantin", group_letter="B")


@pytest.fixture
def team_ger(tournament):
    return Team.objects.create(tournament=tournament, code="GER", name_tr="Almanya", group_letter="B")


@pytest.fixture
def group_slot(tournament, stage_group, team_tur, team_bra):
    """Group slot with both teams pre-filled (as group fixtures normally are)."""
    return BracketSlot.objects.create(
        tournament=tournament, stage=stage_group, position="GroupA-M1",
        scheduled_kickoff=timezone.now() + timedelta(days=5),
        home_team_actual=team_tur, away_team_actual=team_bra,
    )


@pytest.fixture
def r16_slot(tournament, stage_r16):
    """Knockout slot with no teams known yet (forecast slot)."""
    return BracketSlot.objects.create(
        tournament=tournament, stage=stage_r16, position="R16-1",
        scheduled_kickoff=timezone.now() + timedelta(days=20),
        home_source="A Grubu 1.si", away_source="B Grubu 2.si",
    )


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(
        email="player@example.com", username="player@example.com", nickname="Player",
    )
