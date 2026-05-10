"""SlotPredictionForm tests — focuses on time-lock and dropdown behavior.

Model-level validation is covered in test_slot_prediction_model.py — this
file only verifies the rules that live in the form layer.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.predictions.forms import SlotPredictionForm


def _form_kwargs(user, prediction_round, slot):
    return {"user": user, "prediction_round": prediction_round, "slot": slot}


@pytest.mark.django_db
class TestSlotPredictionFormLocks:
    def test_form_rejects_when_round_deadline_passed(
        self, user, prediction_round, r16_slot, team_tur, team_arg
    ):
        prediction_round.deadline = timezone.now() - timedelta(hours=1)
        prediction_round.save()

        form = SlotPredictionForm(
            data={
                "home_team": team_tur.id, "away_team": team_arg.id,
                "home_score": 1, "away_score": 0,
            },
            **_form_kwargs(user, prediction_round, r16_slot),
        )
        assert not form.is_valid()
        assert "süresi doldu" in str(form.errors).lower()

    def test_form_rejects_when_slot_kickoff_passed(
        self, user, prediction_round, r16_slot, team_tur, team_arg
    ):
        r16_slot.scheduled_kickoff = timezone.now() - timedelta(minutes=5)
        r16_slot.save()

        form = SlotPredictionForm(
            data={
                "home_team": team_tur.id, "away_team": team_arg.id,
                "home_score": 1, "away_score": 0,
            },
            **_form_kwargs(user, prediction_round, r16_slot),
        )
        assert not form.is_valid()
        assert "kickoff" in str(form.errors).lower()


@pytest.mark.django_db
class TestSlotPredictionFormGroupSlot:
    def test_team_fields_disabled_for_group_slot(
        self, user, prediction_round, group_slot
    ):
        form = SlotPredictionForm(**_form_kwargs(user, prediction_round, group_slot))
        assert form.fields["home_team"].disabled is True
        assert form.fields["away_team"].disabled is True
        assert form.fields["home_team"].initial == group_slot.home_team_actual
        assert form.fields["away_team"].initial == group_slot.away_team_actual

    def test_disabled_team_fields_use_actual_teams_on_submit(
        self, user, prediction_round, group_slot, team_arg
    ):
        """Even if a malicious POST tries to substitute teams, the disabled
        field forces use of the initial (actual) value."""
        form = SlotPredictionForm(
            data={
                # try to substitute home team with ARG
                "home_team": team_arg.id,
                "away_team": team_arg.id,
                "home_score": 2, "away_score": 1,
            },
            **_form_kwargs(user, prediction_round, group_slot),
        )
        assert form.is_valid(), form.errors
        instance = form.save()
        assert instance.home_team_id == group_slot.home_team_actual_id
        assert instance.away_team_id == group_slot.away_team_actual_id


@pytest.mark.django_db
class TestSlotPredictionFormSave:
    def test_save_creates_new_prediction(
        self, user, prediction_round, r16_slot, team_tur, team_arg
    ):
        form = SlotPredictionForm(
            data={
                "home_team": team_tur.id, "away_team": team_arg.id,
                "home_score": 3, "away_score": 1,
            },
            **_form_kwargs(user, prediction_round, r16_slot),
        )
        assert form.is_valid(), form.errors
        instance = form.save()
        assert instance.pk is not None
        assert instance.home_score == 3

    def test_save_updates_existing_prediction(
        self, user, prediction_round, r16_slot, team_tur, team_arg
    ):
        from apps.predictions.models import SlotPrediction

        existing = SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=0, away_score=0,
            penalty_winner=team_tur, home_penalties=4, away_penalties=2,
        )

        form = SlotPredictionForm(
            data={
                "home_team": team_tur.id, "away_team": team_arg.id,
                "home_score": 2, "away_score": 1,
            },
            instance=existing,
            **_form_kwargs(user, prediction_round, r16_slot),
        )
        assert form.is_valid(), form.errors
        form.save()
        existing.refresh_from_db()
        assert existing.home_score == 2
        assert existing.away_score == 1
        # Decisive prediction → penalty fields cleared
        assert existing.penalty_winner_id is None
        assert existing.home_penalties is None
