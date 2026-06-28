"""A live (in-play) match is treated as a result: /results/ and /matches/ show
the LIVE ("anlık") puan durumu computed off the current running score, badged
CANLI. Going FINISHED only changes the wording/badge — not the data.

A live score is written to ActualResult as the match goes (so the ganyan engine
keeps GanyanScore / MatchPool current); these views just render that live state.
"Currently live" is the same MatchSync-driven signal the homepage CANLI module
uses (apps/liveresults/sync.live_syncs).
"""

from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.liveresults.models import MatchSync
from apps.predictions.models import SlotPrediction
from apps.tournament.models import (
    ActualResult,
    BracketSlot,
    PredictionRound,
    Stage,
    Team,
    Tournament,
)

User = get_user_model()


@pytest.fixture
def t(db):
    return Tournament.objects.create(
        name="WC26", slug="wc26", is_active=True,
        start_date=date(2026, 6, 1), end_date=date(2026, 7, 1),
    )


@pytest.fixture
def r32_stage(t):
    # The group stage must exist too — the legacy SlotScore bridge looks it up
    # for penalty scaling whenever any slot is recomputed by the save signal.
    Stage.objects.create(
        tournament=t, kind=Stage.GROUP, order=0,
        points_exact=6, points_diff=4, points_result=2,
    )
    return Stage.objects.create(
        tournament=t, kind=Stage.R32, order=1,
        points_exact=7, points_diff=5, points_result=3,
    )


@pytest.fixture
def pre_round(t):
    return PredictionRound.objects.create(
        tournament=t, name="Pre-tournament", order=0,
        deadline=timezone.now() + timedelta(days=30), weight="1.00",
    )


def _r32_slot(t, r32_stage, pre_round, *, status, finalized):
    """R32-1 = ZAF–CAN, running 0–0, with one on-fixture predictor (Hemre, 0–0
    exact → currently winning at 0–0). The MatchSync status/finalized decide
    whether the slot reads as live or settled — the standings are the same."""
    zaf = Team.objects.create(tournament=t, code="ZAF", name_tr="Güney Afrika")
    can = Team.objects.create(tournament=t, code="CAN", name_tr="Kanada")
    slot = BracketSlot.objects.create(
        tournament=t, stage=r32_stage, position="R32-1",
        scheduled_kickoff=timezone.now() - timedelta(minutes=30),
    )
    hemre = User.objects.create_user(
        email="hemre@x.com", username="hemre@x.com", nickname="Hemre")
    SlotPrediction.objects.create(
        user=hemre, prediction_round=pre_round, slot=slot,
        home_team=zaf, away_team=can, home_score=0, away_score=0,
    )
    slot.home_team_actual = zaf
    slot.away_team_actual = can
    slot.save(update_fields=["home_team_actual", "away_team_actual"])
    ActualResult.objects.create(slot=slot, home_score=0, away_score=0)
    MatchSync.objects.create(
        slot=slot, external_id="999", status=status, finalized=finalized)
    return slot, hemre


def _match_in_sections(sections, position):
    for sec in sections:
        for m in sec["matches"]:
            if m["slot"].position == position:
                return m
    return None


@pytest.mark.django_db
class TestResultsLiveState:
    def test_results_live_match_shows_live_standings(self, client, t, r32_stage, pre_round):
        slot, hemre = _r32_slot(
            t, r32_stage, pre_round,
            status=MatchSync.STATUS_IN_PLAY, finalized=False)
        r = client.get(reverse("results"))
        m = _match_in_sections(r.context["sections"], "R32-1")
        assert m["is_live"] is True
        # The on-fixture predictor's live score is shown, not deferred.
        assert [e["user"].id for e in m["scores"]] == [hemre.id]
        body = r.content.decode()
        assert "CANLI" in body                  # the live badge
        assert "Canlı puan durumu (1)" in body  # live-aware list label

    def test_results_finished_match_shows_final_standings(self, client, t, r32_stage, pre_round):
        slot, hemre = _r32_slot(
            t, r32_stage, pre_round,
            status=MatchSync.STATUS_FINISHED, finalized=True)
        r = client.get(reverse("results"))
        m = _match_in_sections(r.context["sections"], "R32-1")
        assert m["is_live"] is False
        assert [e["user"].id for e in m["scores"]] == [hemre.id]
        body = r.content.decode()
        assert "CANLI" not in body
        assert "Oyuncu puanları (1)" in body

    def test_match_detail_live_shows_live_standings(self, client, t, r32_stage, pre_round):
        slot, hemre = _r32_slot(
            t, r32_stage, pre_round,
            status=MatchSync.STATUS_PAUSED, finalized=False)
        r = client.get(reverse("match_detail", args=[slot.id]))
        assert r.context["is_live"] is True
        # Standings computed live off the running score — Hemre's 0–0 is winning.
        assert [e["user"].id for e in r.context["user_payouts"]] == [hemre.id]
        body = r.content.decode()
        assert "CANLI" in body
        assert "Canlı Puan Durumu" in body
        assert "anlık" in body  # the "değişebilir" disclaimer

    def test_match_detail_finished_shows_final_standings(self, client, t, r32_stage, pre_round):
        slot, hemre = _r32_slot(
            t, r32_stage, pre_round,
            status=MatchSync.STATUS_FINISHED, finalized=True)
        r = client.get(reverse("match_detail", args=[slot.id]))
        assert r.context["is_live"] is False
        assert [e["user"].id for e in r.context["user_payouts"]] == [hemre.id]
        body = r.content.decode()
        assert "CANLI" not in body
        assert "Oyuncu Puanları" in body
