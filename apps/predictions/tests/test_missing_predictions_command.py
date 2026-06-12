"""missing_predictions — gap report over open rounds' editable stages."""

from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from apps.predictions.models import SlotPrediction


def _run() -> str:
    out = StringIO()
    call_command("missing_predictions", stdout=out)
    return out.getvalue()


@pytest.fixture
def r16_pred(user, prediction_round, r16_slot, team_tur, team_arg):
    return SlotPrediction.objects.create(
        user=user, prediction_round=prediction_round, slot=r16_slot,
        home_team=team_tur, away_team=team_arg, home_score=2, away_score=1,
    )


@pytest.mark.django_db
class TestMissingPredictions:
    def test_partial_user_lists_missing_positions(
        self, user, prediction_round, group_slot, r16_slot, r16_pred,
    ):
        output = _run()
        # r16 predicted, group not → only the group slot is reported.
        assert "GroupA-M1" in output
        assert "R16-1" not in output

    def test_complete_user_marked_ok(
        self, user, prediction_round, group_slot, r16_slot, r16_pred,
        team_tur, team_bra,
    ):
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=1, away_score=0,
        )
        output = _run()
        assert f"OK   {user.nickname}" in output

    def test_user_without_any_predictions_flagged(
        self, prediction_round, group_slot, r16_slot,
    ):
        get_user_model().objects.create_user(
            email="idle@example.com", username="idle@example.com", nickname="Idle",
        )
        output = _run()
        assert "Idle: no predictions at all" in output

    def test_closed_stage_gaps_not_reported(
        self, user, prediction_round, stage_group, group_slot, r16_slot, r16_pred,
    ):
        # GROUP closed mid-round → its gap is not actionable, user is OK.
        prediction_round.editable_stages.remove(stage_group)
        output = _run()
        assert "GroupA-M1" not in output
        assert f"OK   {user.nickname}" in output

    def test_no_open_rounds(self, prediction_round, group_slot, r16_slot):
        prediction_round.deadline = timezone.now() - timedelta(hours=1)
        prediction_round.save()
        assert "No open rounds" in _run()
