"""Tests for the once-per-(user, round) bracket completion event.

Conftest gives us a `prediction_round` whose editable_stages are GROUP + R16,
plus exactly one slot in each (`group_slot`, `r16_slot`) — i.e. a 2-slot
"bracket" for testing completion behavior.
"""

import pytest
from django.urls import reverse

from apps.predictions.models import BracketCompletionEvent, SlotPrediction
from apps.predictions.views import _check_and_mark_bracket_complete


@pytest.mark.django_db
class TestBracketCompletionHelper:
    def test_partial_bracket_does_not_complete(
        self, user, prediction_round, group_slot, r16_slot,
        team_tur, team_bra,
    ):
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        assert _check_and_mark_bracket_complete(user, prediction_round) is False
        assert not BracketCompletionEvent.objects.filter(
            user=user, prediction_round=prediction_round,
        ).exists()

    def test_full_bracket_completes_and_creates_event(
        self, user, prediction_round, group_slot, r16_slot,
        team_tur, team_bra, team_arg,
    ):
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=3, away_score=0,
        )
        assert _check_and_mark_bracket_complete(user, prediction_round) is True
        assert BracketCompletionEvent.objects.filter(
            user=user, prediction_round=prediction_round,
        ).count() == 1

    def test_second_call_returns_false_so_event_fires_once(
        self, user, prediction_round, group_slot, r16_slot,
        team_tur, team_bra, team_arg,
    ):
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=3, away_score=0,
        )
        assert _check_and_mark_bracket_complete(user, prediction_round) is True
        assert _check_and_mark_bracket_complete(user, prediction_round) is False
        # Still exactly one marker row.
        assert BracketCompletionEvent.objects.filter(
            user=user, prediction_round=prediction_round,
        ).count() == 1

    def test_round_with_no_editable_slots_does_not_complete(
        self, user, prediction_round,
    ):
        # No slots in the DB → editable_slot_count == 0 → guard short-circuits.
        assert _check_and_mark_bracket_complete(user, prediction_round) is False
        assert not BracketCompletionEvent.objects.exists()


@pytest.mark.django_db
class TestBracketCompletionHtmxIntegration:
    def test_htmx_response_includes_event_when_save_completes_bracket(
        self, client, user, prediction_round, group_slot, r16_slot,
        team_tur, team_bra, team_arg,
    ):
        # Pre-fill one of the two editable slots — the next save will be the
        # one that completes the bracket.
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )

        client.force_login(user)
        r = client.post(
            reverse("slot_prediction_save", args=[prediction_round.id, r16_slot.id]),
            {
                "home_team": team_tur.id, "away_team": team_arg.id,
                "home_score": 3, "away_score": 0,
            },
            HTTP_HX_REQUEST="true",
        )
        assert r.status_code == 200
        assert b"bracket_tamamlandi" in r.content
        assert BracketCompletionEvent.objects.filter(
            user=user, prediction_round=prediction_round,
        ).exists()

    def test_htmx_response_omits_event_when_save_does_not_complete_bracket(
        self, client, user, prediction_round, group_slot, r16_slot,
        team_tur, team_bra,
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
        # tahmin_kaydedildi still fires; bracket_tamamlandi should not.
        assert b"tahmin_kaydedildi" in r.content
        assert b"bracket_tamamlandi" not in r.content
        assert not BracketCompletionEvent.objects.exists()

    def test_htmx_response_omits_event_on_subsequent_save_after_completion(
        self, client, user, prediction_round, group_slot, r16_slot,
        team_tur, team_bra, team_arg,
    ):
        # Complete the bracket first (creates the marker).
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=user, prediction_round=prediction_round, slot=r16_slot,
            home_team=team_tur, away_team=team_arg, home_score=3, away_score=0,
        )
        BracketCompletionEvent.objects.create(user=user, prediction_round=prediction_round)

        # Now edit the group slot — event should NOT fire again.
        client.force_login(user)
        r = client.post(
            reverse("slot_prediction_save", args=[prediction_round.id, group_slot.id]),
            {
                "home_team": team_tur.id, "away_team": team_bra.id,
                "home_score": 4, "away_score": 0,
            },
            HTTP_HX_REQUEST="true",
        )
        assert r.status_code == 200
        assert b"bracket_tamamlandi" not in r.content
