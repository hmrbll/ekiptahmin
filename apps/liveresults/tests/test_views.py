"""Tests for the homepage live-scores partial view."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.liveresults.models import MatchSync
from apps.tournament.models import ActualResult, BracketSlot


@pytest.fixture
def in_play_slot(tournament, group_stage, teams):
    s = BracketSlot.objects.create(
        tournament=tournament, stage=group_stage, position="GroupA-M1",
        scheduled_kickoff=timezone.now() - timedelta(minutes=20),
        home_team_actual=teams["TUR"], away_team_actual=teams["BRA"],
    )
    MatchSync.objects.create(slot=s, external_id="1", status="IN_PLAY", minute=55, injury_time=4)
    ActualResult.objects.create(slot=s, home_score=1, away_score=0, source="API")
    return s


def test_live_scores_renders_in_play_match(client, in_play_slot):
    resp = client.get(reverse("live_scores"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "CANLI" in body
    assert "Türkiye" in body and "Brezilya" in body
    assert "55+4" in body  # live minute + injury time (90+N′ style)


def test_live_scores_empty_when_nothing_live(client, tournament):
    resp = client.get(reverse("live_scores"))
    assert resp.status_code == 200
    assert "CANLI" not in resp.content.decode()


def test_match_past_cap_not_shown(client, tournament, group_stage, teams):
    s = BracketSlot.objects.create(
        tournament=tournament, stage=group_stage, position="GroupA-M5",
        scheduled_kickoff=timezone.now() - timedelta(hours=2, minutes=30),
        home_team_actual=teams["TUR"], away_team_actual=teams["BRA"],
    )
    MatchSync.objects.create(slot=s, external_id="2", status="IN_PLAY")
    ActualResult.objects.create(slot=s, home_score=1, away_score=0, source="API")
    resp = client.get(reverse("live_scores"))
    assert "CANLI" not in resp.content.decode()


def test_finished_match_not_shown_as_live(client, in_play_slot):
    in_play_slot.live_sync.status = "FINISHED"
    in_play_slot.live_sync.finalized = True
    in_play_slot.live_sync.save()
    resp = client.get(reverse("live_scores"))
    assert "CANLI" not in resp.content.decode()
