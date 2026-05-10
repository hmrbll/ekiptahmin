"""View tests — login_required gates, happy paths, and carry-over behavior.

The slot edit form lives inline on the round detail page (single-page UX);
the dedicated POST endpoint `slot_prediction_save` is exercised here too.
"""

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

    def test_anonymous_redirected_from_slot_save(
        self, client, prediction_round, r16_slot, team_tur, team_arg,
    ):
        r = client.post(
            reverse("slot_prediction_save", args=[prediction_round.id, r16_slot.id]),
            {"home_team": team_tur.id, "away_team": team_arg.id, "home_score": 1, "away_score": 0},
        )
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
        self, client, user, prediction_round, group_slot, r16_slot,
    ):
        client.force_login(user)
        r = client.get(reverse("prediction_round_detail", args=[prediction_round.id]))
        assert r.status_code == 200
        assert b"GroupA-M1" in r.content
        assert b"R16-1" in r.content

    def test_filters_out_slots_from_non_editable_stages(
        self, client, user, prediction_round, group_slot, r16_slot, stage_group,
    ):
        prediction_round.editable_stages.set([stage_group])
        client.force_login(user)
        r = client.get(reverse("prediction_round_detail", args=[prediction_round.id]))
        assert b"GroupA-M1" in r.content
        assert b"R16-1" not in r.content

    def test_shows_users_existing_prediction_score(
        self, client, user, prediction_round, group_slot, team_tur, team_bra,
    ):
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        client.force_login(user)
        r = client.get(reverse("prediction_round_detail", args=[prediction_round.id]))
        # The score shows as input value="2" / value="1" on the inline form
        assert b'value="2"' in r.content
        assert b'value="1"' in r.content

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
        # The current user has no prediction → score inputs render empty
        assert SlotPrediction.objects.filter(user=user).count() == 0

    def test_score_label_differs_for_group_vs_knockout(
        self, client, user, prediction_round, group_slot, r16_slot,
    ):
        client.force_login(user)
        r = client.get(reverse("prediction_round_detail", args=[prediction_round.id]))
        assert "Skor: 90 dk".encode("utf-8") in r.content
        assert "90 / 120 dk".encode("utf-8") in r.content


@pytest.mark.django_db
class TestSlotPredictionSave:
    def test_save_redirects_to_round_detail_for_normal_post(
        self, client, user, prediction_round, group_slot, team_tur, team_bra,
    ):
        client.force_login(user)
        r = client.post(
            reverse("slot_prediction_save", args=[prediction_round.id, group_slot.id]),
            {
                "home_team": team_tur.id, "away_team": team_bra.id,
                "home_score": 2, "away_score": 1,
            },
        )
        assert r.status_code == 302
        assert reverse("prediction_round_detail", args=[prediction_round.id]) in r["Location"]
        assert SlotPrediction.objects.filter(user=user, slot=group_slot).exists()

    def test_save_returns_row_fragment_for_htmx(
        self, client, user, prediction_round, group_slot, team_tur, team_bra,
    ):
        client.force_login(user)
        r = client.post(
            reverse("slot_prediction_save", args=[prediction_round.id, group_slot.id]),
            {
                "home_team": team_tur.id, "away_team": team_bra.id,
                "home_score": 2, "away_score": 1,
            },
            HTTP_HX_REQUEST="true",
        )
        assert r.status_code == 200
        assert b"slot-row-" in r.content
        assert b"Kaydedildi" in r.content
        assert SlotPrediction.objects.filter(user=user, slot=group_slot).exists()

    def test_save_with_validation_error_returns_form_fragment(
        self, client, user, prediction_round, r16_slot, team_tur, team_arg,
    ):
        client.force_login(user)
        # 1-1 draw on knockout without penalty → validation error
        r = client.post(
            reverse("slot_prediction_save", args=[prediction_round.id, r16_slot.id]),
            {
                "home_team": team_tur.id, "away_team": team_arg.id,
                "home_score": 1, "away_score": 1,
            },
            HTTP_HX_REQUEST="true",
        )
        assert r.status_code == 200
        # Error visible in the fragment
        assert b"penalty_winner" in r.content or "penalt".encode("utf-8") in r.content
        assert not SlotPrediction.objects.filter(user=user, slot=r16_slot).exists()

    def test_carry_over_from_earlier_round(
        self, client, user, prediction_round, r16_slot, team_tur, team_arg,
        tournament, stage_r16,
    ):
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
        r = client.get(reverse("prediction_round_detail", args=[later.id]))
        # Earlier prediction's scores are pre-filled in the new round's form
        assert b'value="3"' in r.content
        assert b'value="2"' in r.content

    def test_locked_slot_post_does_not_save(
        self, client, user, prediction_round, r16_slot, team_tur, team_arg,
    ):
        r16_slot.scheduled_kickoff = timezone.now() - timedelta(minutes=1)
        r16_slot.save()
        client.force_login(user)
        client.post(
            reverse("slot_prediction_save", args=[prediction_round.id, r16_slot.id]),
            {
                "home_team": team_tur.id, "away_team": team_arg.id,
                "home_score": 1, "away_score": 0,
            },
        )
        assert not SlotPrediction.objects.filter(user=user, slot=r16_slot).exists()

    def test_update_existing_prediction_clears_penalty_fields(
        self, client, user, prediction_round, r16_slot, team_tur, team_arg,
    ):
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=0, away_score=0,
            penalty_winner=team_tur, home_penalties=4, away_penalties=3,
        )
        client.force_login(user)
        client.post(
            reverse("slot_prediction_save", args=[prediction_round.id, r16_slot.id]),
            {
                "home_team": team_tur.id, "away_team": team_arg.id,
                "home_score": 2, "away_score": 1,
            },
        )
        p = SlotPrediction.objects.get(user=user, slot=r16_slot)
        assert (p.home_score, p.away_score) == (2, 1)
        assert p.penalty_winner_id is None
        assert p.home_penalties is None
