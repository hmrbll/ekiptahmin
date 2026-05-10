"""PredictionRound.is_open / is_pending_results behavior tests."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.tournament.models import (
    ActualResult,
    BracketSlot,
    PredictionRound,
    Stage,
    Tournament,
)


@pytest.fixture
def tournament(db):
    return Tournament.objects.create(
        name="T", slug="t",
        start_date=date(2026, 6, 1), end_date=date(2026, 7, 1),
    )


@pytest.fixture
def stage_group(tournament):
    return Stage.objects.create(
        tournament=tournament, kind=Stage.GROUP, order=0,
        points_exact=6, points_diff=4, points_result=2,
        penalty_loser_pct=Decimal("0.60"),
    )


@pytest.fixture
def stage_r32(tournament):
    return Stage.objects.create(
        tournament=tournament, kind=Stage.R32, order=1,
        points_exact=7, points_diff=5, points_result=3,
        penalty_loser_pct=Decimal("0.60"),
    )


@pytest.fixture
def group_slot(tournament, stage_group):
    return BracketSlot.objects.create(
        tournament=tournament, stage=stage_group, position="GroupA-M1",
        scheduled_kickoff=timezone.now() + timedelta(days=5),
    )


@pytest.mark.django_db
class TestRoundIsOpen:
    def test_open_when_in_window_no_dependency(self, tournament):
        r = PredictionRound.objects.create(
            tournament=tournament, name="Pre", order=0,
            deadline=timezone.now() + timedelta(days=10),
            weight=Decimal("1.00"),
        )
        assert r.is_open is True
        assert r.is_pending_results is False

    def test_closed_after_deadline(self, tournament):
        r = PredictionRound.objects.create(
            tournament=tournament, name="Past", order=0,
            deadline=timezone.now() - timedelta(hours=1),
            weight=Decimal("1.00"),
        )
        assert r.is_open is False

    def test_closed_before_opens_at(self, tournament):
        r = PredictionRound.objects.create(
            tournament=tournament, name="Future", order=0,
            opens_at=timezone.now() + timedelta(hours=1),
            deadline=timezone.now() + timedelta(days=10),
            weight=Decimal("1.00"),
        )
        assert r.is_open is False

    def test_pending_when_dependency_unresolved(self, tournament, stage_group, group_slot):
        r = PredictionRound.objects.create(
            tournament=tournament, name="After group", order=1,
            deadline=timezone.now() + timedelta(days=10),
            weight=Decimal("0.85"),
            depends_on_stage=stage_group,
        )
        assert r.is_open is False
        assert r.is_pending_results is True

    def test_open_after_dependency_resolved(self, tournament, stage_group, group_slot, db):
        r = PredictionRound.objects.create(
            tournament=tournament, name="After group", order=1,
            deadline=timezone.now() + timedelta(days=10),
            weight=Decimal("0.85"),
            depends_on_stage=stage_group,
        )
        ActualResult.objects.create(slot=group_slot, home_score=2, away_score=1)
        assert r.is_open is True
        assert r.is_pending_results is False

    def test_dependency_with_multiple_slots_all_must_be_resolved(
        self, tournament, stage_group, group_slot
    ):
        # Add a second group slot without a result
        BracketSlot.objects.create(
            tournament=tournament, stage=stage_group, position="GroupA-M2",
            scheduled_kickoff=timezone.now() + timedelta(days=6),
        )
        r = PredictionRound.objects.create(
            tournament=tournament, name="After group", order=1,
            deadline=timezone.now() + timedelta(days=10),
            weight=Decimal("0.85"),
            depends_on_stage=stage_group,
        )
        ActualResult.objects.create(slot=group_slot, home_score=2, away_score=1)
        # Still pending — second slot has no result
        assert r.is_open is False
        assert r.is_pending_results is True

    def test_pending_only_when_inside_time_window(self, tournament, stage_group, group_slot):
        # Past deadline → not open and not pending (just closed)
        r = PredictionRound.objects.create(
            tournament=tournament, name="Closed past", order=1,
            deadline=timezone.now() - timedelta(hours=1),
            weight=Decimal("0.85"),
            depends_on_stage=stage_group,
        )
        assert r.is_open is False
        assert r.is_pending_results is False
