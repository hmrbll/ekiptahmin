"""SlotPrediction model validation tests."""

import pytest
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import IntegrityError

from apps.predictions.models import SlotPrediction


@pytest.mark.django_db
class TestSlotPredictionGroupSlot:
    def test_valid_group_prediction_passes(self, user, prediction_round, group_slot, team_tur, team_bra):
        p = SlotPrediction(
            user=user, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        p.full_clean()  # should not raise

    def test_group_team_substitution_rejected(
        self, user, prediction_round, group_slot, team_tur, team_arg
    ):
        """Can't predict ARG instead of BRA in a known group slot."""
        p = SlotPrediction(
            user=user, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_arg, home_score=1, away_score=0,
        )
        with pytest.raises(ValidationError) as exc:
            p.full_clean()
        assert "away_team" in exc.value.error_dict

    def test_group_draw_does_not_require_penalties(
        self, user, prediction_round, group_slot, team_tur, team_bra
    ):
        """Group matches can end in a draw — no penalty fields needed."""
        p = SlotPrediction(
            user=user, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=1, away_score=1,
        )
        p.full_clean()


@pytest.mark.django_db
class TestSlotPredictionKnockoutSlot:
    def test_valid_knockout_decisive_prediction(
        self, user, prediction_round, r16_slot, team_tur, team_arg
    ):
        p = SlotPrediction(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=2, away_score=0,
        )
        p.full_clean()

    def test_knockout_draw_requires_penalty_winner(
        self, user, prediction_round, r16_slot, team_tur, team_arg
    ):
        p = SlotPrediction(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=1, away_score=1,
        )
        with pytest.raises(ValidationError) as exc:
            p.full_clean()
        assert "penalty_winner" in exc.value.error_dict

    def test_knockout_draw_with_full_penalty_data_passes(
        self, user, prediction_round, r16_slot, team_tur, team_arg
    ):
        p = SlotPrediction(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=1, away_score=1,
            penalty_winner=team_tur, home_penalties=4, away_penalties=2,
        )
        p.full_clean()

    def test_penalty_winner_must_be_one_of_two_teams(
        self, user, prediction_round, r16_slot, team_tur, team_arg, team_ger
    ):
        p = SlotPrediction(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=1, away_score=1,
            penalty_winner=team_ger, home_penalties=4, away_penalties=3,
        )
        with pytest.raises(ValidationError) as exc:
            p.full_clean()
        assert "penalty_winner" in exc.value.error_dict

    def test_penalty_shootout_cannot_be_a_draw(
        self, user, prediction_round, r16_slot, team_tur, team_arg
    ):
        p = SlotPrediction(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=0, away_score=0,
            penalty_winner=team_tur, home_penalties=3, away_penalties=3,
        )
        with pytest.raises(ValidationError) as exc:
            p.full_clean()
        assert "away_penalties" in exc.value.error_dict

    def test_penalty_fields_forbidden_when_not_a_draw(
        self, user, prediction_round, r16_slot, team_tur, team_arg
    ):
        p = SlotPrediction(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=2, away_score=0,
            penalty_winner=team_tur, home_penalties=4, away_penalties=2,
        )
        with pytest.raises(ValidationError) as exc:
            p.full_clean()
        assert "penalty_winner" in exc.value.error_dict


@pytest.mark.django_db
class TestSlotPredictionStructuralRules:
    def test_same_team_for_both_sides_rejected(
        self, user, prediction_round, r16_slot, team_tur
    ):
        p = SlotPrediction(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_tur, home_score=1, away_score=0,
        )
        with pytest.raises(ValidationError) as exc:
            p.full_clean()
        assert "away_team" in exc.value.error_dict

    def test_stage_not_in_round_editable_stages_rejected(
        self, user, tournament, stage_group, group_slot, team_tur, team_bra
    ):
        from datetime import timedelta
        from decimal import Decimal

        from django.utils import timezone

        from apps.tournament.models import PredictionRound, Stage

        # Round that only allows the FINAL stage to be edited
        final = Stage.objects.create(
            tournament=tournament, kind=Stage.FINAL, order=6,
            points_exact=20, points_diff=14, points_result=7,
            penalty_loser_pct=Decimal("0.60"),
        )
        narrow_round = PredictionRound.objects.create(
            tournament=tournament, name="Late round", order=5,
            deadline=timezone.now() + timedelta(days=30),
            weight=Decimal("0.50"),
        )
        narrow_round.editable_stages.set([final])

        p = SlotPrediction(
            user=user, prediction_round=narrow_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=1, away_score=0,
        )
        with pytest.raises(ValidationError) as exc:
            p.full_clean()
        # Non-field error on purpose: the user-facing form has no `slot`
        # field and would crash on an unknown error key.
        assert NON_FIELD_ERRORS in exc.value.error_dict

    def test_unique_constraint_on_user_round_slot(
        self, user, prediction_round, r16_slot, team_tur, team_arg
    ):
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=2, away_score=0,
        )
        with pytest.raises(IntegrityError):
            SlotPrediction.objects.create(
                user=user, prediction_round=prediction_round, slot=r16_slot,
                home_team=team_arg, away_team=team_tur, home_score=1, away_score=3,
            )

    def test_team_from_other_tournament_rejected(
        self, user, prediction_round, group_slot, team_tur
    ):
        from datetime import date

        from apps.tournament.models import Team, Tournament

        other = Tournament.objects.create(
            name="Other", slug="other",
            start_date=date(2030, 1, 1), end_date=date(2030, 2, 1),
        )
        outsider = Team.objects.create(tournament=other, code="ESP", name_tr="İspanya")

        p = SlotPrediction(
            user=user, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=outsider, home_score=1, away_score=0,
        )
        with pytest.raises(ValidationError):
            p.full_clean()
