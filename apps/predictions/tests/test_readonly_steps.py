"""Read-only wizard steps for locked stages/rounds.

Product rule: a stage the admin closed mid-round (removed from
editable_stages — e.g. GROUP at tournament kickoff) stays visible in the
wizard for users who predicted it in that round, rendered read-only. The
same read-only rendering applies when the whole round's deadline has
passed or a slot's kickoff is in the past.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.predictions.models import SlotPrediction


@pytest.fixture
def group_pred(user, prediction_round, group_slot, team_tur, team_bra):
    return SlotPrediction.objects.create(
        user=user, prediction_round=prediction_round, slot=group_slot,
        home_team=team_tur, away_team=team_bra, home_score=2, away_score=0,
    )


def _close_group_stage(prediction_round, stage_group):
    prediction_round.editable_stages.remove(stage_group)


@pytest.mark.django_db
class TestGroupStageClosedMidRound:
    def test_group_step_stays_visible_read_only(
        self, client, user, prediction_round, stage_group, group_slot, group_pred,
    ):
        _close_group_stage(prediction_round, stage_group)
        client.force_login(user)

        r = client.get(reverse("predict_group_step", args=[prediction_round.id, "A"]))

        assert r.status_code == 200
        content = r.content.decode()
        # No editable form for the group slot — static display instead.
        assert f'data-pred-form="{group_slot.id}"' not in content
        assert "Türkiye" in content and "Brezilya" in content
        assert "Kilitli" in content

    def test_group_step_hidden_without_own_predictions(
        self, client, user, prediction_round, stage_group, group_slot,
    ):
        _close_group_stage(prediction_round, stage_group)
        client.force_login(user)

        r = client.get(reverse("predict_group_step", args=[prediction_round.id, "A"]))

        assert r.status_code == 302
        assert r.url == reverse("predict_round_entry", args=[prediction_round.id])

    def test_entry_lands_on_first_editable_step(
        self, client, user, prediction_round, stage_group, group_pred, r16_slot,
    ):
        _close_group_stage(prediction_round, stage_group)
        client.force_login(user)

        r = client.get(reverse("predict_round_entry", args=[prediction_round.id]))

        # Group steps exist (read-only) but entry skips them for the first
        # stage the user can still edit.
        assert r.status_code == 302
        assert r.url == reverse(
            "predict_knockout_stage_step", args=[prediction_round.id, "R16"])

    def test_nav_pills_keep_group_steps_with_lock_marker(
        self, client, user, prediction_round, stage_group, group_pred, r16_slot,
    ):
        _close_group_stage(prediction_round, stage_group)
        client.force_login(user)

        r = client.get(reverse(
            "predict_knockout_stage_step", args=[prediction_round.id, "R16"]))

        content = r.content.decode()
        assert "Grup Özet" in content
        assert "🔒" in content

    def test_save_on_closed_stage_is_rejected(
        self, client, user, prediction_round, stage_group, group_slot, group_pred,
        team_tur, team_bra,
    ):
        _close_group_stage(prediction_round, stage_group)
        client.force_login(user)

        client.post(
            reverse("slot_prediction_save", args=[prediction_round.id, group_slot.id]),
            {"home_team": team_tur.id, "away_team": team_bra.id,
             "home_score": 0, "away_score": 5},
            HTTP_HX_REQUEST="true",
        )

        group_pred.refresh_from_db()
        assert (group_pred.home_score, group_pred.away_score) == (2, 0)

    def test_groups_summary_read_only_with_standings(
        self, client, user, prediction_round, stage_group, group_slot, group_pred,
    ):
        _close_group_stage(prediction_round, stage_group)
        client.force_login(user)

        r = client.get(reverse("predict_groups_summary", args=[prediction_round.id]))

        assert r.status_code == 200
        content = r.content.decode()
        assert "sadece görüntüleme" in content
        assert f'data-pred-form="{group_slot.id}"' not in content
        # Standings table still rendered from the user's predictions.
        assert "Sıralama" in content or "standings" in content


@pytest.mark.django_db
class TestClosedRoundIsReadOnly:
    def test_knockout_rows_render_read_only_after_deadline(
        self, client, user, prediction_round, r16_slot, team_tur, team_arg,
    ):
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=1, away_score=0,
        )
        prediction_round.deadline = timezone.now() - timedelta(hours=1)
        prediction_round.save()
        client.force_login(user)

        r = client.get(reverse(
            "predict_knockout_stage_step", args=[prediction_round.id, "R16"]))

        assert r.status_code == 200
        content = r.content.decode()
        assert f'data-pred-form="{r16_slot.id}"' not in content
        assert "Türkiye" in content and "Arjantin" in content
        assert "Kilitli" in content

    def test_unpredicted_slot_shows_empty_state(
        self, client, user, prediction_round, r16_slot,
    ):
        prediction_round.deadline = timezone.now() - timedelta(hours=1)
        prediction_round.save()
        client.force_login(user)

        r = client.get(reverse(
            "predict_knockout_stage_step", args=[prediction_round.id, "R16"]))

        assert r.status_code == 200
        assert "Tahmin yapılmadı" in r.content.decode()
