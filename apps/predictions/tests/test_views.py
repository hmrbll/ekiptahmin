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
class TestWizardSteps:
    def test_round_entry_redirects_to_first_group_when_groups_editable(
        self, client, user, prediction_round, group_slot, r16_slot,
    ):
        client.force_login(user)
        r = client.get(reverse("predict_round_entry", args=[prediction_round.id]))
        assert r.status_code == 302
        assert "/group/A/" in r["Location"]

    def test_round_entry_jumps_to_knockout_when_no_group_editable(
        self, client, user, prediction_round, r16_slot, stage_r16,
    ):
        prediction_round.editable_stages.set([stage_r16])
        client.force_login(user)
        r = client.get(reverse("predict_round_entry", args=[prediction_round.id]))
        assert r.status_code == 302
        assert "/knockout/" in r["Location"]

    def test_group_step_shows_only_that_groups_slots(
        self, client, user, prediction_round, group_slot, r16_slot,
    ):
        client.force_login(user)
        r = client.get(reverse("predict_group_step", args=[prediction_round.id, "A"]))
        assert r.status_code == 200
        assert b"GroupA-M1" in r.content
        # Knockout slot belongs to the knockout step, not the group step
        assert b"R16-1" not in r.content

    def test_group_step_shows_user_prediction_score(
        self, client, user, prediction_round, group_slot, team_tur, team_bra,
    ):
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        client.force_login(user)
        r = client.get(reverse("predict_group_step", args=[prediction_round.id, "A"]))
        assert b'value="2"' in r.content
        assert b'value="1"' in r.content

    def test_knockout_step_shows_knockout_slots_only(
        self, client, user, prediction_round, group_slot, r16_slot,
    ):
        client.force_login(user)
        r = client.get(reverse("predict_knockout_step", args=[prediction_round.id]))
        assert r.status_code == 200
        assert b"R16-1" in r.content
        assert b"GroupA-M1" not in r.content
        assert "90 / 120 dk".encode("utf-8") in r.content

    def test_groups_summary_lists_all_group_blocks(
        self, client, user, prediction_round, group_slot,
    ):
        client.force_login(user)
        r = client.get(reverse("predict_groups_summary", args=[prediction_round.id]))
        assert r.status_code == 200
        assert b"GroupA-M1" in r.content
        assert "Tüm Grupların".encode("utf-8") in r.content


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
        # Knockout step renders the carried-over scores in the form inputs.
        r = client.get(reverse("predict_knockout_step", args=[later.id]))
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
