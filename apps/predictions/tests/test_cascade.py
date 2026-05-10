"""Cascade behavior tests — knockout slots derive teams from earlier predictions."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.predictions.forms import SlotPredictionForm
from apps.predictions.models import SlotPrediction
from apps.tournament.models import BracketSlot, PredictionRound, Stage


@pytest.fixture
def stage_qf(tournament):
    return Stage.objects.create(
        tournament=tournament, kind=Stage.QF, order=3,
        points_exact=14, points_diff=9, points_result=5,
        penalty_loser_pct=Decimal("0.60"),
    )


@pytest.fixture
def round_with_all_stages(prediction_round, stage_qf):
    prediction_round.editable_stages.add(stage_qf)
    return prediction_round


@pytest.fixture
def r16_slot_cascaded(tournament, stage_r16, r16_slot):
    """R16 slot whose home/away cascade from two R32 source slots."""
    r32_stage = Stage.objects.create(
        tournament=tournament, kind=Stage.R32, order=1,
        points_exact=7, points_diff=5, points_result=3,
        penalty_loser_pct=Decimal("0.60"),
    )
    home_src = BracketSlot.objects.create(
        tournament=tournament, stage=r32_stage, position="R32-1",
        scheduled_kickoff=timezone.now() + timedelta(days=10),
    )
    away_src = BracketSlot.objects.create(
        tournament=tournament, stage=r32_stage, position="R32-3",
        scheduled_kickoff=timezone.now() + timedelta(days=10),
    )
    r16_slot.home_source_slot = home_src
    r16_slot.home_source_kind = BracketSlot.SOURCE_KIND_WINNER
    r16_slot.away_source_slot = away_src
    r16_slot.away_source_kind = BracketSlot.SOURCE_KIND_WINNER
    r16_slot.save()
    return r16_slot, home_src, away_src, r32_stage


@pytest.mark.django_db
class TestCascadeForm:
    def test_cascade_blocked_when_source_predictions_missing(
        self, user, prediction_round, r16_slot_cascaded, stage_r16, r32_stage_via_fixture=None,
    ):
        r16, home_src, away_src, r32_stage = r16_slot_cascaded
        prediction_round.editable_stages.add(r32_stage)
        form = SlotPredictionForm(
            user=user, prediction_round=prediction_round, slot=r16,
        )
        assert form.cascade_blocked_on == [home_src, away_src]

    def test_cascade_derives_winner_from_user_prediction(
        self, user, prediction_round, r16_slot_cascaded, team_tur, team_bra, team_arg, team_ger,
    ):
        r16, home_src, away_src, r32_stage = r16_slot_cascaded
        prediction_round.editable_stages.add(r32_stage)

        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=home_src,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=away_src,
            home_team=team_arg, away_team=team_ger, home_score=0, away_score=3,
        )

        form = SlotPredictionForm(
            user=user, prediction_round=prediction_round, slot=r16,
        )
        assert form.cascade_blocked_on == []
        assert form.fields["home_team"].initial == team_tur  # R32-1 winner
        assert form.fields["away_team"].initial == team_ger  # R32-3 winner
        assert form.fields["home_team"].disabled is True
        assert form.fields["away_team"].disabled is True

    def test_cascade_uses_penalty_winner_for_draw_source(
        self, user, prediction_round, r16_slot_cascaded, team_tur, team_bra, team_arg, team_ger,
    ):
        r16, home_src, away_src, r32_stage = r16_slot_cascaded
        prediction_round.editable_stages.add(r32_stage)

        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=home_src,
            home_team=team_tur, away_team=team_bra, home_score=1, away_score=1,
            penalty_winner=team_bra, home_penalties=3, away_penalties=4,
        )
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=away_src,
            home_team=team_arg, away_team=team_ger, home_score=2, away_score=0,
        )

        form = SlotPredictionForm(
            user=user, prediction_round=prediction_round, slot=r16,
        )
        assert form.fields["home_team"].initial == team_bra  # penalty winner

    def test_cascade_loser_kind_derives_loser(
        self, user, tournament, prediction_round, r16_slot_cascaded, team_tur, team_bra, team_arg, team_ger,
    ):
        """Verifies LOSER kind for the third-place match pattern."""
        r16, home_src, away_src, r32_stage = r16_slot_cascaded
        # Repurpose r16 as a LOSER-cascade slot (mimicking Third place match).
        r16.home_source_kind = BracketSlot.SOURCE_KIND_LOSER
        r16.save()
        prediction_round.editable_stages.add(r32_stage)

        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=home_src,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=away_src,
            home_team=team_arg, away_team=team_ger, home_score=0, away_score=3,
        )

        form = SlotPredictionForm(
            user=user, prediction_round=prediction_round, slot=r16,
        )
        assert form.fields["home_team"].initial == team_bra  # loser of R32-1
        assert form.fields["away_team"].initial == team_ger  # winner of R32-3

    def test_cascaded_slot_form_blocks_submission_when_source_missing(
        self, user, prediction_round, r16_slot_cascaded, team_tur, team_arg,
    ):
        r16, home_src, away_src, r32_stage = r16_slot_cascaded
        prediction_round.editable_stages.add(r32_stage)
        form = SlotPredictionForm(
            data={
                "home_team": team_tur.id, "away_team": team_arg.id,
                "home_score": 2, "away_score": 1,
            },
            user=user, prediction_round=prediction_round, slot=r16,
        )
        assert not form.is_valid()
        assert "önceki round" in str(form.errors).lower() or "tahmin et" in str(form.errors).lower()


@pytest.mark.django_db
class TestSlotPredictionWinnerLoser:
    def test_winner_for_decisive_home_win(
        self, user, prediction_round, group_slot, team_tur, team_bra,
    ):
        p = SlotPrediction(
            user=user, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=3, away_score=1,
        )
        assert p.winner_team() == team_tur
        assert p.loser_team() == team_bra

    def test_winner_for_away_win(
        self, user, prediction_round, group_slot, team_tur, team_bra,
    ):
        p = SlotPrediction(
            user=user, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=0, away_score=2,
        )
        assert p.winner_team() == team_bra
        assert p.loser_team() == team_tur

    def test_winner_for_draw_with_penalty_winner(
        self, user, prediction_round, r16_slot, team_tur, team_arg,
    ):
        p = SlotPrediction(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=1, away_score=1,
            penalty_winner=team_arg, home_penalties=3, away_penalties=5,
        )
        assert p.winner_team() == team_arg
        assert p.loser_team() == team_tur

    def test_winner_returns_none_for_draw_without_penalty_winner(
        self, user, prediction_round, group_slot, team_tur, team_bra,
    ):
        p = SlotPrediction(
            user=user, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=1, away_score=1,
        )
        assert p.winner_team() is None
        assert p.loser_team() is None
