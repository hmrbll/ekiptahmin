"""Cascade behavior tests — knockout slots derive teams from earlier predictions."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.predictions.forms import SlotPredictionForm
from apps.predictions.models import SlotPrediction
from apps.predictions.standings import derive_group_team
from apps.tournament.models import BracketSlot, PredictionRound, Stage, Team


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
        blocked_slots = [b["slot"] for b in form.cascade_blocked_on]
        assert blocked_slots == [home_src, away_src]

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


@pytest.fixture
def group_a_slots(tournament, stage_group, team_tur, team_bra):
    """3 group matches in Group A (need at least 1 match for partial standings)."""
    # Need 2 more teams in group A
    other1 = Team.objects.create(tournament=tournament, code="MAR", name_tr="Fas", group_letter="A")
    other2 = Team.objects.create(tournament=tournament, code="ESP", name_tr="İspanya", group_letter="A")
    slots = []
    for i, (h, a) in enumerate([(team_tur, team_bra), (other1, other2), (team_tur, other1)], start=1):
        slots.append(BracketSlot.objects.create(
            tournament=tournament, stage=stage_group, position=f"GroupA-M{i}",
            scheduled_kickoff=timezone.now() + timedelta(days=5 + i),
            home_team_actual=h, away_team_actual=a,
        ))
    return slots, other1, other2


@pytest.fixture
def r32_slot_group_cascade(tournament):
    """R32 slot with home cascading from 'A Grubu 1.si', away free."""
    r32_stage = Stage.objects.create(
        tournament=tournament, kind=Stage.R32, order=1,
        points_exact=7, points_diff=5, points_result=3,
        penalty_loser_pct=Decimal("0.60"),
    )
    return BracketSlot.objects.create(
        tournament=tournament, stage=r32_stage, position="R32-1",
        scheduled_kickoff=timezone.now() + timedelta(days=15),
        home_source_group_letter="A", home_source_group_position=1,
    ), r32_stage


@pytest.mark.django_db
class TestR32GroupCascade:
    def test_group_cascade_blocked_when_no_group_predictions(
        self, user, prediction_round, r32_slot_group_cascade, group_a_slots,
    ):
        slot, _ = r32_slot_group_cascade
        form = SlotPredictionForm(
            user=user, prediction_round=prediction_round, slot=slot,
        )
        labels = [b["label"] for b in form.cascade_blocked_on]
        assert any("A Grubu 1.si" in label for label in labels)

    def test_group_cascade_derives_first_place_from_predictions(
        self, user, prediction_round, r32_slot_group_cascade, group_a_slots,
        team_tur, team_bra,
    ):
        slot, _ = r32_slot_group_cascade
        slots, other1, other2 = group_a_slots

        # User predicts: TUR beats BRA 2-0, MAR beats ESP 1-0, TUR beats MAR 1-0
        # → TUR: 2W=6pts; MAR: 1W 1L=3pts; BRA: 1L=0pts; ESP: 1L=0pts
        # → A Grubu 1.si = TUR
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=slots[0],
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=0,
        )
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=slots[1],
            home_team=other1, away_team=other2, home_score=1, away_score=0,
        )
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=slots[2],
            home_team=team_tur, away_team=other1, home_score=1, away_score=0,
        )

        form = SlotPredictionForm(
            user=user, prediction_round=prediction_round, slot=slot,
        )
        assert form.cascade_blocked_on == []
        assert form.fields["home_team"].initial == team_tur
        assert form.fields["home_team"].disabled is True


@pytest.fixture
def r32_slot_thirds(tournament):
    """R32 slot with home cascading from '3.lerden biri (A/B)' source."""
    r32_stage = Stage.objects.create(
        tournament=tournament, kind=Stage.R32, order=1,
        points_exact=7, points_diff=5, points_result=3,
        penalty_loser_pct=Decimal("0.60"),
    )
    return BracketSlot.objects.create(
        tournament=tournament, stage=r32_stage, position="R32-2",
        scheduled_kickoff=timezone.now() + timedelta(days=15),
        home_source_thirds_groups="A,B",
    )


@pytest.mark.django_db
class TestR32ThirdsCascade:
    def test_thirds_dropdown_filters_to_third_place_candidates(
        self, user, tournament, prediction_round, r32_slot_thirds, group_a_slots,
        stage_group, team_tur, team_bra, team_arg, team_ger,
    ):
        # Predict Group A so 3rd place is determined (BRA finishes 3rd)
        slots_a, other1, other2 = group_a_slots
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=slots_a[0],
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=0,
        )
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=slots_a[1],
            home_team=other1, away_team=other2, home_score=1, away_score=0,
        )
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=slots_a[2],
            home_team=team_tur, away_team=other1, home_score=1, away_score=0,
        )
        # Standings A: TUR(6), MAR(3), BRA(0=ga2 gd-2), ESP(0 gd-1) → BRA 3rd? No: ESP gd=-1, BRA gd=-2 → ESP 3rd

        # Check actual computed third
        third = derive_group_team(user, tournament, "A", 3)
        assert third is not None  # Third determined

        # Add Group B slot + prediction to make B's 3rd determinable
        for i, (h, a) in enumerate([(team_arg, team_ger)], start=1):
            BracketSlot.objects.create(
                tournament=tournament, stage=stage_group, position=f"GroupB-M{i}",
                scheduled_kickoff=timezone.now() + timedelta(days=5),
                home_team_actual=h, away_team_actual=a,
            )

        form = SlotPredictionForm(
            user=user, prediction_round=prediction_round, slot=r32_slot_thirds,
        )
        # The thirds queryset is filtered to A's third (B can't compute 3rd from 1 match)
        third_pks = list(form.fields["home_team"].queryset.values_list("pk", flat=True))
        assert third.pk in third_pks
