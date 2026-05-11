"""Leaderboard aggregation + tie-explanation tests + view smoke test."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.predictions.models import SlotPrediction
from apps.scoring.leaderboard import describe_ties, leaderboard_for_tournament
from apps.scoring.models import SlotScore
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
        name="WC26", slug="wc26", start_date=date(2026, 6, 1), end_date=date(2026, 7, 1),
        is_active=True,
    )


@pytest.fixture
def group_stage(t):
    return Stage.objects.create(
        tournament=t, kind=Stage.GROUP, order=0,
        points_exact=6, points_diff=4, points_result=2,
        penalty_loser_pct=Decimal("0.60"),
    )


@pytest.fixture
def pre_round(t, group_stage):
    pr = PredictionRound.objects.create(
        tournament=t, name="Pre-tournament", order=0,
        deadline=timezone.now() + timedelta(days=30),
        weight=Decimal("1.00"),
    )
    pr.editable_stages.set([group_stage])
    return pr


@pytest.fixture
def after_group_round(t, group_stage):
    pr = PredictionRound.objects.create(
        tournament=t, name="After Group", order=1,
        deadline=timezone.now() + timedelta(days=40),
        weight=Decimal("0.85"),
        depends_on_stage=group_stage,
    )
    pr.editable_stages.set([group_stage])  # synthetic — keeps editing legal here
    return pr


@pytest.fixture
def tur(t):
    return Team.objects.create(tournament=t, code="TUR", name_tr="Türkiye", group_letter="A")


@pytest.fixture
def bra(t):
    return Team.objects.create(tournament=t, code="BRA", name_tr="Brezilya", group_letter="A")


@pytest.fixture
def slot1(t, group_stage, tur, bra):
    return BracketSlot.objects.create(
        tournament=t, stage=group_stage, position="GroupA-M1",
        scheduled_kickoff=timezone.now() + timedelta(days=10),
        home_team_actual=tur, away_team_actual=bra,
    )


@pytest.fixture
def slot2(t, group_stage, tur, bra):
    return BracketSlot.objects.create(
        tournament=t, stage=group_stage, position="GroupA-M2",
        scheduled_kickoff=timezone.now() + timedelta(days=11),
        home_team_actual=bra, away_team_actual=tur,
    )


@pytest.mark.django_db
class TestLeaderboardAggregation:
    def test_empty_when_no_scores(self, t):
        assert leaderboard_for_tournament(t) == []

    def test_single_user_ranks_first(self, t, pre_round, slot1, tur, bra):
        u = User.objects.create_user(email="a@x.com", username="a@x.com", nickname="Ali")
        SlotPrediction.objects.create(
            user=u, prediction_round=pre_round, slot=slot1,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        ActualResult.objects.create(slot=slot1, home_score=2, away_score=1)
        entries = leaderboard_for_tournament(t)
        assert len(entries) == 1
        assert entries[0].user == u
        assert entries[0].rank == 1
        assert entries[0].total == Decimal("6")
        # Per-round breakdown: round 0 = 6, no later rounds → 0
        assert entries[0].per_round[0] == Decimal("6")

    def test_two_users_ordered_by_total(
        self, t, pre_round, slot1, slot2, tur, bra,
    ):
        u1 = User.objects.create_user(email="a@x.com", username="a@x.com", nickname="Ali")
        u2 = User.objects.create_user(email="b@x.com", username="b@x.com", nickname="Veli")
        # Ali predicts exact on slot1.
        SlotPrediction.objects.create(
            user=u1, prediction_round=pre_round, slot=slot1,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        # Veli predicts result-only on slot1 (right outcome, wrong score & diff).
        SlotPrediction.objects.create(
            user=u2, prediction_round=pre_round, slot=slot1,
            home_team=tur, away_team=bra, home_score=3, away_score=0,
        )
        ActualResult.objects.create(slot=slot1, home_score=2, away_score=1)
        entries = leaderboard_for_tournament(t)
        assert [e.user for e in entries] == [u1, u2]
        assert entries[0].rank == 1
        assert entries[1].rank == 2

    def test_truly_tied_users_share_rank(
        self, t, pre_round, slot1, tur, bra,
    ):
        u1 = User.objects.create_user(email="a@x.com", username="a@x.com", nickname="A")
        u2 = User.objects.create_user(email="b@x.com", username="b@x.com", nickname="B")
        # Both predict exact on slot1 in pre-round → identical totals and
        # identical tiebreaker tuples.
        for u in (u1, u2):
            SlotPrediction.objects.create(
                user=u, prediction_round=pre_round, slot=slot1,
                home_team=tur, away_team=bra, home_score=2, away_score=1,
            )
        ActualResult.objects.create(slot=slot1, home_score=2, away_score=1)
        entries = leaderboard_for_tournament(t)
        assert entries[0].rank == entries[1].rank == 1


@pytest.mark.django_db
class TestTieDescriptions:
    def test_no_notes_when_all_unique(self, t, pre_round, slot1, slot2, tur, bra):
        u1 = User.objects.create_user(email="a@x.com", username="a@x.com", nickname="A")
        u2 = User.objects.create_user(email="b@x.com", username="b@x.com", nickname="B")
        SlotPrediction.objects.create(
            user=u1, prediction_round=pre_round, slot=slot1,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=u2, prediction_round=pre_round, slot=slot1,
            home_team=tur, away_team=bra, home_score=3, away_score=0,
        )
        ActualResult.objects.create(slot=slot1, home_score=2, away_score=1)
        notes = describe_ties(leaderboard_for_tournament(t))
        assert notes == []

    def test_note_when_truly_tied(self, t, pre_round, slot1, tur, bra):
        u1 = User.objects.create_user(email="a@x.com", username="a@x.com", nickname="A")
        u2 = User.objects.create_user(email="b@x.com", username="b@x.com", nickname="B")
        for u in (u1, u2):
            SlotPrediction.objects.create(
                user=u, prediction_round=pre_round, slot=slot1,
                home_team=tur, away_team=bra, home_score=2, away_score=1,
            )
        ActualResult.objects.create(slot=slot1, home_score=2, away_score=1)
        notes = describe_ties(leaderboard_for_tournament(t))
        assert len(notes) == 1
        assert "A" in notes[0] and "B" in notes[0]
        assert "ortak sıra" in notes[0]


@pytest.mark.django_db
class TestLeaderboardView:
    def test_anonymous_redirected(self, client):
        r = client.get(reverse("leaderboard"))
        assert r.status_code == 302
        assert "/auth/login/" in r["Location"]

    def test_authenticated_sees_table(
        self, client, t, pre_round, slot1, tur, bra,
    ):
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        SlotPrediction.objects.create(
            user=u, prediction_round=pre_round, slot=slot1,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        ActualResult.objects.create(slot=slot1, home_score=2, away_score=1)
        client.force_login(u)
        r = client.get(reverse("leaderboard"))
        assert r.status_code == 200
        assert b"Skor Tablosu" in r.content
        assert b"Me" in r.content

    def test_view_renders_empty_state(self, client, t):
        u = User.objects.create_user(email="x@x.com", username="x@x.com", nickname="X")
        client.force_login(u)
        r = client.get(reverse("leaderboard"))
        assert r.status_code == 200
        assert "Henüz puan kaydedilmedi".encode("utf-8") in r.content
