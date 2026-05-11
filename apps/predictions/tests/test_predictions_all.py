"""Tests for /predictions/all/ — public match-by-match predictions page."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.predictions.models import SlotPrediction
from apps.tournament.models import ActualResult, BracketSlot

User = get_user_model()


def _past_slot(tournament, stage_group, team_tur, team_bra, position="GroupA-M2"):
    return BracketSlot.objects.create(
        tournament=tournament, stage=stage_group, position=position,
        scheduled_kickoff=timezone.now() - timedelta(hours=2),
        home_team_actual=team_tur, away_team_actual=team_bra,
    )


@pytest.mark.django_db
class TestPredictionsAll:
    def test_anonymous_can_view(self, client, tournament):
        r = client.get(reverse("predictions_all"))
        assert r.status_code == 200
        assert "Tüm Tahminler".encode("utf-8") in r.content

    def test_pre_lock_match_hides_predictions_but_shows_count(
        self, client, tournament, prediction_round, group_slot, team_tur, team_bra,
    ):
        u1 = User.objects.create_user(email="a@x.com", username="a@x.com", nickname="A")
        u2 = User.objects.create_user(email="b@x.com", username="b@x.com", nickname="B")
        SlotPrediction.objects.create(
            user=u1, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=u2, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=3, away_score=0,
        )
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        # Match itself appears
        assert "GroupA-M1" in body
        # Count is shown
        assert "2 oyuncu tahmin etti" in body
        # But the actual predicted scores must not leak.
        assert "2–1" not in body
        assert "3–0" not in body

    def test_locked_match_reveals_predictions(
        self, client, tournament, stage_group, prediction_round, team_tur, team_bra,
    ):
        # Use a past kickoff so the slot is locked.
        past = _past_slot(tournament, stage_group, team_tur, team_bra, "GroupA-M2")
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        SlotPrediction.objects.create(
            user=u, prediction_round=prediction_round, slot=past,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        assert "GroupA-M2" in body
        assert "Me" in body
        assert "2–1" in body  # prediction revealed

    def test_match_with_result_reveals_predictions_even_pre_kickoff(
        self, client, tournament, prediction_round, group_slot, team_tur, team_bra,
    ):
        """Admin entering a result early (test mode) should reveal predictions
        even when scheduled_kickoff is still in the future.
        """
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        SlotPrediction.objects.create(
            user=u, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        ActualResult.objects.create(slot=group_slot, home_score=2, away_score=1)
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        assert "GroupA-M1" in body
        assert "2–1" in body

    def test_skips_slots_without_resolved_teams(
        self, client, tournament, prediction_round, r16_slot, group_slot,
    ):
        # r16_slot has no home_team_actual / away_team_actual yet.
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        # Group slot still appears (teams set); R16 doesn't.
        assert "GroupA-M1" in body
        assert "R16-1" not in body
