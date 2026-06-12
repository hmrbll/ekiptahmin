"""revalidate_predictions — backfill sweep over open rounds.

Covers the gap save-time invalidation can't reach: stale rows created
before the invalidation feature existed (or by a since-fixed bug) are
deleted retroactively, but only while their round is open.
"""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.predictions.models import SlotPrediction
from apps.tournament.models import BracketSlot, PredictionRound, Stage


@pytest.fixture
def stage_r32(tournament):
    return Stage.objects.create(
        tournament=tournament, kind=Stage.R32, order=1,
        points_exact=8, points_diff=5, points_result=2,
        penalty_loser_pct=Decimal("0.60"),
    )


@pytest.fixture
def full_round(prediction_round, stage_group, stage_r32, stage_r16):
    prediction_round.editable_stages.set([stage_group, stage_r32, stage_r16])
    return prediction_round


@pytest.fixture
def r32_slot(tournament, stage_r32, team_tur, team_bra):
    return BracketSlot.objects.create(
        tournament=tournament, stage=stage_r32, position="R32-1",
        scheduled_kickoff=timezone.now() + timedelta(days=12),
        home_team_actual=team_tur, away_team_actual=team_bra,
    )


@pytest.fixture
def r16_cascaded(tournament, stage_r16, r32_slot):
    """R16 slot whose home side is the winner of R32-1; away side free."""
    return BracketSlot.objects.create(
        tournament=tournament, stage=stage_r16, position="R16-1",
        scheduled_kickoff=timezone.now() + timedelta(days=16),
        home_source_slot=r32_slot, home_source_kind=BracketSlot.SOURCE_KIND_WINNER,
    )


@pytest.fixture
def stale_bracket(user, full_round, r32_slot, r16_cascaded,
                  team_tur, team_bra, team_arg):
    """R32-1 says BRA wins, but the R16 row still has TUR — stale, as if
    written before invalidation existed."""
    SlotPrediction.objects.create(
        user=user, prediction_round=full_round, slot=r32_slot,
        home_team=team_tur, away_team=team_bra, home_score=0, away_score=1,
    )
    return SlotPrediction.objects.create(
        user=user, prediction_round=full_round, slot=r16_cascaded,
        home_team=team_tur, away_team=team_arg, home_score=2, away_score=0,
    )


def _run(*args) -> str:
    out = StringIO()
    call_command("revalidate_predictions", *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
class TestRevalidatePredictions:
    def test_deletes_stale_rows_in_open_round(self, stale_bracket, r16_cascaded, user):
        output = _run()

        assert not SlotPrediction.objects.filter(user=user, slot=r16_cascaded).exists()
        assert "R16-1" in output
        assert "1 stale prediction(s) deleted." in output

    def test_consistent_rows_survive(self, user, full_round, r32_slot, r16_cascaded,
                                     team_tur, team_bra, team_arg):
        SlotPrediction.objects.create(
            user=user, prediction_round=full_round, slot=r32_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=user, prediction_round=full_round, slot=r16_cascaded,
            home_team=team_tur, away_team=team_arg, home_score=1, away_score=0,
        )

        output = _run()

        assert SlotPrediction.objects.filter(user=user, slot=r16_cascaded).exists()
        assert "0 stale prediction(s) deleted." in output

    def test_dry_run_reports_but_keeps_rows(self, stale_bracket, r16_cascaded, user):
        output = _run("--dry-run")

        assert SlotPrediction.objects.filter(user=user, slot=r16_cascaded).exists()
        assert "R16-1" in output
        assert "would be deleted (dry run)" in output

    def test_closed_round_is_never_touched(self, stale_bracket, full_round,
                                           r16_cascaded, user):
        full_round.deadline = timezone.now() - timedelta(hours=1)
        full_round.save()

        output = _run()

        assert SlotPrediction.objects.filter(user=user, slot=r16_cascaded).exists()
        assert "No open rounds" in output

    def test_closed_round_untouched_even_when_another_round_is_open(
        self, stale_bracket, full_round, r16_cascaded, user,
        tournament, stage_group, stage_r32, stage_r16,
    ):
        full_round.deadline = timezone.now() - timedelta(hours=1)
        full_round.save()
        open_round = PredictionRound.objects.create(
            tournament=tournament, name="Open later round", order=7,
            deadline=timezone.now() + timedelta(days=5), weight=Decimal("0.50"),
        )
        open_round.editable_stages.set([stage_r32, stage_r16])

        _run()

        # The stale row lives in the CLOSED round — scored history, kept.
        assert SlotPrediction.objects.filter(user=user, slot=r16_cascaded).exists()
