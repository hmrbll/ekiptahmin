"""View tests — login_required gates, happy paths, and carry-over behavior."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.predictions.models import SlotPrediction
from apps.tournament.models import PredictionRound


@pytest.mark.django_db
class TestAccessControl:
    def test_anonymous_redirected_from_round_list(self, client):
        r = client.get(reverse("prediction_rounds"))
        assert r.status_code == 302
        assert "/auth/login/" in r["Location"]

    def test_anonymous_redirected_from_round_detail(self, client, prediction_round):
        r = client.get(reverse("prediction_round_detail", args=[prediction_round.id]))
        assert r.status_code == 302

    def test_anonymous_redirected_from_slot_edit(
        self, client, prediction_round, r16_slot
    ):
        r = client.get(reverse("slot_prediction_edit", args=[prediction_round.id, r16_slot.id]))
        assert r.status_code == 302


@pytest.mark.django_db
class TestRoundListView:
    def test_lists_rounds_for_active_tournament(self, client, user, prediction_round):
        client.force_login(user)
        r = client.get(reverse("prediction_rounds"))
        assert r.status_code == 200
        assert b"Pre-tournament" in r.content

    def test_no_active_tournament_renders_placeholder(self, client, user, tournament):
        tournament.is_active = False
        tournament.save()
        client.force_login(user)
        r = client.get(reverse("prediction_rounds"))
        assert r.status_code == 200
        assert b"Aktif turnuva yok" in r.content


@pytest.mark.django_db
class TestRoundDetailView:
    def test_shows_only_editable_stage_slots(
        self, client, user, prediction_round, group_slot, r16_slot, tournament,
    ):
        client.force_login(user)
        r = client.get(reverse("prediction_round_detail", args=[prediction_round.id]))
        assert r.status_code == 200
        assert b"GroupA-M1" in r.content
        assert b"R16-1" in r.content

    def test_filters_out_slots_from_non_editable_stages(
        self, client, user, prediction_round, group_slot, r16_slot, tournament, stage_group,
    ):
        # Restrict round to group only — R16 slot should disappear
        prediction_round.editable_stages.set([stage_group])
        client.force_login(user)
        r = client.get(reverse("prediction_round_detail", args=[prediction_round.id]))
        assert b"GroupA-M1" in r.content
        assert b"R16-1" not in r.content

    def test_shows_users_existing_prediction(
        self, client, user, prediction_round, r16_slot, team_tur, team_arg,
    ):
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=2, away_score=1,
        )
        client.force_login(user)
        r = client.get(reverse("prediction_round_detail", args=[prediction_round.id]))
        # Score is rendered as "2 – 1" (en-dash) in the polished template.
        assert "2 – 1".encode("utf-8") in r.content

    def test_other_users_predictions_not_shown(
        self, client, user, prediction_round, r16_slot, team_tur, team_arg, db,
    ):
        from django.contrib.auth import get_user_model
        other = get_user_model().objects.create_user(
            email="other@example.com", username="other@example.com",
        )
        SlotPrediction.objects.create(
            user=other, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=2, away_score=1,
        )
        client.force_login(user)
        r = client.get(reverse("prediction_round_detail", args=[prediction_round.id]))
        assert "2 – 1".encode("utf-8") not in r.content


@pytest.mark.django_db
class TestSlotEditView:
    def test_create_new_prediction_redirects_to_round_detail(
        self, client, user, prediction_round, r16_slot, team_tur, team_arg,
    ):
        client.force_login(user)
        r = client.post(
            reverse("slot_prediction_edit", args=[prediction_round.id, r16_slot.id]),
            {
                "home_team": team_tur.id, "away_team": team_arg.id,
                "home_score": 2, "away_score": 1,
            },
        )
        assert r.status_code == 302
        assert reverse("prediction_round_detail", args=[prediction_round.id]) in r["Location"]
        assert SlotPrediction.objects.filter(user=user, slot=r16_slot).exists()

    def test_carry_over_from_earlier_round_pre_fills_form(
        self, client, user, prediction_round, r16_slot, team_tur, team_arg,
        tournament, stage_r16,
    ):
        # Round 1 (after current = order 0)
        later = PredictionRound.objects.create(
            tournament=tournament, name="After group", order=1,
            deadline=timezone.now() + timedelta(days=20), weight=Decimal("0.85"),
        )
        later.editable_stages.set([stage_r16])

        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=3, away_score=2,
        )
        client.force_login(user)
        r = client.get(reverse("slot_prediction_edit", args=[later.id, r16_slot.id]))
        assert r.status_code == 200
        # The earlier prediction should be the form initial
        assert b'value="3"' in r.content
        assert b'value="2"' in r.content

    def test_locked_slot_post_does_not_save(
        self, client, user, prediction_round, r16_slot, team_tur, team_arg,
    ):
        r16_slot.scheduled_kickoff = timezone.now() - timedelta(minutes=1)
        r16_slot.save()

        client.force_login(user)
        r = client.post(
            reverse("slot_prediction_edit", args=[prediction_round.id, r16_slot.id]),
            {
                "home_team": team_tur.id, "away_team": team_arg.id,
                "home_score": 1, "away_score": 0,
            },
        )
        # Form re-rendered, not redirected
        assert r.status_code == 200
        assert not SlotPrediction.objects.filter(user=user, slot=r16_slot).exists()

    def test_update_existing_prediction(
        self, client, user, prediction_round, r16_slot, team_tur, team_arg,
    ):
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=0, away_score=0,
            penalty_winner=team_tur, home_penalties=4, away_penalties=3,
        )
        client.force_login(user)
        r = client.post(
            reverse("slot_prediction_edit", args=[prediction_round.id, r16_slot.id]),
            {
                "home_team": team_tur.id, "away_team": team_arg.id,
                "home_score": 2, "away_score": 1,
            },
        )
        assert r.status_code == 302
        assert SlotPrediction.objects.filter(user=user, slot=r16_slot).count() == 1
        p = SlotPrediction.objects.get(user=user, slot=r16_slot)
        assert (p.home_score, p.away_score) == (2, 1)
        assert p.penalty_winner_id is None
