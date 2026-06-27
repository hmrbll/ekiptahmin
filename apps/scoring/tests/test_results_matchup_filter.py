"""Results & match-detail player lists show only picks on the ACTUAL fixture.

A knockout slot gathers every player's bracket pick, so most picks listed under
it are really for a different matchup (different teams reached this slot). Those
can't score this match and the ganyan tablosu already excludes them from N / the
breakdown — so they must not leak into the per-match "Oyuncu Puanları" lists
either. Group slots have a fixed fixture, so the filter is a no-op there.

Mirrors the knockout matchup convention enforced on the all-predictions card and
in the engine (`ganyan._matchup_correct`).
"""

from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

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
def group_stage(t):
    return Stage.objects.create(
        tournament=t, kind=Stage.GROUP, order=0,
        points_exact=6, points_diff=4, points_result=2,
    )


@pytest.fixture
def r32_stage(t, group_stage):
    # group_stage must exist — the legacy SlotScore bridge looks it up for
    # penalty scaling whenever any slot is recomputed.
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


def _team(t, code, name):
    return Team.objects.create(tournament=t, code=code, name_tr=name)


def _user(nick):
    email = f"{nick.lower()}@x.com"
    return User.objects.create_user(email=email, username=email, nickname=nick)


def _match_in_sections(sections, position):
    for sec in sections:
        for m in sec["matches"]:
            if m["slot"].position == position:
                return m
    return None


@pytest.mark.django_db
class TestResultsMatchupFilter:
    def _setup_r32(self, t, r32_stage, pre_round):
        """R32-1 resolves to ZAF-CAN. Three players predicted this slot in their
        brackets: one on the real fixture, two on other matchups."""
        zaf = _team(t, "ZAF", "Güney Afrika")
        can = _team(t, "CAN", "Kanada")
        kor = _team(t, "KOR", "Güney Kore")
        cze = _team(t, "CZE", "Çek Cumhuriyeti")
        bih = _team(t, "BIH", "Bosna Hersek")

        slot = BracketSlot.objects.create(
            tournament=t, stage=r32_stage, position="R32-1",
            scheduled_kickoff=timezone.now() - timedelta(hours=3),
        )

        hemre = _user("Hemre")
        akin = _user("Akın")
        ibo = _user("İbo")
        # Real fixture, exact score.
        SlotPrediction.objects.create(
            user=hemre, prediction_round=pre_round, slot=slot,
            home_team=zaf, away_team=can, home_score=2, away_score=1,
        )
        # Different matchups (other teams reached this slot in their brackets).
        SlotPrediction.objects.create(
            user=akin, prediction_round=pre_round, slot=slot,
            home_team=kor, away_team=can, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=ibo, prediction_round=pre_round, slot=slot,
            home_team=cze, away_team=bih, home_score=0, away_score=0,
        )

        # Slot resolves, result comes in → signal scores the slot.
        slot.home_team_actual = zaf
        slot.away_team_actual = can
        slot.save(update_fields=["home_team_actual", "away_team_actual"])
        ActualResult.objects.create(slot=slot, home_score=2, away_score=1)
        return slot, hemre

    def test_results_list_drops_off_fixture_picks(self, client, t, r32_stage, pre_round):
        slot, hemre = self._setup_r32(t, r32_stage, pre_round)

        r = client.get(reverse("results"))
        assert r.status_code == 200
        m = _match_in_sections(r.context["sections"], "R32-1")
        assert m is not None
        # Only the on-fixture pick survives; the two bracket picks are gone.
        assert [e["user"].id for e in m["scores"]] == [hemre.id]
        # All three still counted as predictors (for the empty-state note).
        assert m["prediction_count"] == 3

        body = r.content.decode()
        assert "Oyuncu puanları (1)" in body
        assert "Akın" not in body and "İbo" not in body

    def test_match_detail_drops_off_fixture_picks(self, client, t, r32_stage, pre_round):
        slot, hemre = self._setup_r32(t, r32_stage, pre_round)

        r = client.get(reverse("match_detail", args=[slot.id]))
        assert r.status_code == 200
        assert [e["user"].id for e in r.context["user_payouts"]] == [hemre.id]
        body = r.content.decode()
        assert "Akın" not in body and "İbo" not in body

    def test_results_note_when_nobody_hit_the_matchup(self, client, t, r32_stage, pre_round):
        """All picks off-fixture → no player rows, but the count-aware note shows."""
        zaf = _team(t, "ZAF", "Güney Afrika")
        can = _team(t, "CAN", "Kanada")
        kor = _team(t, "KOR", "Güney Kore")
        slot = BracketSlot.objects.create(
            tournament=t, stage=r32_stage, position="R32-1",
            scheduled_kickoff=timezone.now() - timedelta(hours=3),
        )
        SlotPrediction.objects.create(
            user=_user("Akın"), prediction_round=pre_round, slot=slot,
            home_team=kor, away_team=can, home_score=1, away_score=0,
        )
        slot.home_team_actual = zaf
        slot.away_team_actual = can
        slot.save(update_fields=["home_team_actual", "away_team_actual"])
        ActualResult.objects.create(slot=slot, home_score=2, away_score=1)

        r = client.get(reverse("results"))
        m = _match_in_sections(r.context["sections"], "R32-1")
        assert m["scores"] == []
        assert m["prediction_count"] == 1
        assert "1 oyuncu tahmin etti, ama kimse bu eşleşmeyi tutturamadı." in r.content.decode()

    def test_group_slot_is_unaffected(self, client, t, group_stage, pre_round):
        """Group fixtures are fixed, so every pick is on-fixture → all shown."""
        tur = _team(t, "TUR", "Türkiye")
        bra = _team(t, "BRA", "Brezilya")
        slot = BracketSlot.objects.create(
            tournament=t, stage=group_stage, position="GroupA-M1",
            scheduled_kickoff=timezone.now() - timedelta(hours=3),
            home_team_actual=tur, away_team_actual=bra,
        )
        a = _user("Ali")
        b = _user("Veli")
        SlotPrediction.objects.create(
            user=a, prediction_round=pre_round, slot=slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=b, prediction_round=pre_round, slot=slot,
            home_team=tur, away_team=bra, home_score=0, away_score=0,
        )
        ActualResult.objects.create(slot=slot, home_score=2, away_score=1)

        r = client.get(reverse("results"))
        m = _match_in_sections(r.context["sections"], "GroupA-M1")
        assert {e["user"].id for e in m["scores"]} == {a.id, b.id}
