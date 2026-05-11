"""Home page view: anonymous landing vs authenticated overview."""

from datetime import date, timedelta
from decimal import Decimal

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


# --- Self-contained DB fixtures (kept off the accounts/conftest.py so they
# don't bleed into the other accounts tests). ---


@pytest.fixture
def t(db):
    return Tournament.objects.create(
        name="WC", slug="wc", start_date=date(2026, 6, 1), end_date=date(2026, 7, 1),
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
def tur(t):
    return Team.objects.create(tournament=t, code="TUR", name_tr="Türkiye", group_letter="A")


@pytest.fixture
def bra(t):
    return Team.objects.create(tournament=t, code="BRA", name_tr="Brezilya", group_letter="A")


def _slot(t, stage, position, kickoff, home, away):
    return BracketSlot.objects.create(
        tournament=t, stage=stage, position=position,
        scheduled_kickoff=kickoff,
        home_team_actual=home, away_team_actual=away,
    )


@pytest.mark.django_db
class TestHomeAnonymous:
    def test_renders_landing_for_guest(self, client):
        r = client.get(reverse("home"))
        assert r.status_code == 200
        body = r.content.decode("utf-8")
        # Marketing copy
        assert "Giriş yap" in body
        assert "104" in body  # one of the stat blocks


@pytest.mark.django_db
class TestHomeAuthenticated:
    def test_no_tournament_renders_safely(self, client, db):
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        client.force_login(u)
        r = client.get(reverse("home"))
        assert r.status_code == 200
        # Header greets the user even without tournament data.
        assert "Merhaba Me" in r.content.decode("utf-8")

    def test_shows_only_available_upcoming_matches(
        self, client, t, group_stage, tur, bra,
    ):
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        now = timezone.now()
        # 2 upcoming matches (fewer than the 4 cap) — display all 2.
        _slot(t, group_stage, "GroupA-M1", now + timedelta(days=1), tur, bra)
        _slot(t, group_stage, "GroupA-M2", now + timedelta(days=2), bra, tur)
        # One past match — must NOT appear in upcoming.
        _slot(t, group_stage, "GroupA-M3", now - timedelta(days=1), tur, bra)

        client.force_login(u)
        r = client.get(reverse("home"))
        body = r.content.decode("utf-8")
        assert "GroupA-M1" in body
        assert "GroupA-M2" in body
        assert "GroupA-M3" not in body
        # "2 maç" count chip reflects the actual available number.
        assert "2 maç" in body

    def test_upcoming_match_shows_user_prediction(
        self, client, t, group_stage, pre_round, tur, bra,
    ):
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        now = timezone.now()
        slot = _slot(t, group_stage, "GroupA-M1", now + timedelta(days=1), tur, bra)
        SlotPrediction.objects.create(
            user=u, prediction_round=pre_round, slot=slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        client.force_login(u)
        r = client.get(reverse("home"))
        body = r.content.decode("utf-8")
        assert "Tahminin" in body
        assert "2–1" in body

    def test_recent_results_show_viewer_score(
        self, client, t, group_stage, pre_round, tur, bra,
    ):
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        now = timezone.now()
        slot = _slot(t, group_stage, "GroupA-M1", now - timedelta(days=1), tur, bra)
        SlotPrediction.objects.create(
            user=u, prediction_round=pre_round, slot=slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        ActualResult.objects.create(slot=slot, home_score=2, away_score=1)
        client.force_login(u)
        r = client.get(reverse("home"))
        body = r.content.decode("utf-8")
        assert "GroupA-M1" in body
        # `floatformat` renders 6.00 as "6,00" under tr locale.
        assert "Aldığın" in body
        assert "6,00" in body

    def test_leaderboard_module_lists_top_users_with_highlight(
        self, client, t, group_stage, pre_round, tur, bra,
    ):
        me = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        rival = User.objects.create_user(email="r@x.com", username="r@x.com", nickname="Rival")
        now = timezone.now()
        slot = _slot(t, group_stage, "GroupA-M1", now - timedelta(days=1), tur, bra)
        SlotPrediction.objects.create(
            user=me, prediction_round=pre_round, slot=slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=rival, prediction_round=pre_round, slot=slot,
            home_team=tur, away_team=bra, home_score=3, away_score=0,
        )
        ActualResult.objects.create(slot=slot, home_score=2, away_score=1)
        client.force_login(me)
        r = client.get(reverse("home"))
        body = r.content.decode("utf-8")
        assert "Me" in body
        assert "Rival" in body
        # Own row gets the highlight class.
        assert "border-emerald-400/20" in body

    def test_leaderboard_module_caps_at_twelve(
        self, client, t, group_stage, pre_round, tur, bra,
    ):
        slot = _slot(t, group_stage, "GroupA-M1",
                     timezone.now() - timedelta(days=1), tur, bra)
        ActualResult.objects.create(slot=slot, home_score=2, away_score=1)
        # 15 users predict — page should still render only 12.
        for i in range(15):
            u = User.objects.create_user(
                email=f"u{i}@x.com", username=f"u{i}@x.com", nickname=f"U{i}",
            )
            SlotPrediction.objects.create(
                user=u, prediction_round=pre_round, slot=slot,
                home_team=tur, away_team=bra, home_score=i % 3, away_score=i % 2,
            )

        viewer = User.objects.get(email="u0@x.com")
        client.force_login(viewer)
        r = client.get(reverse("home"))
        body = r.content.decode("utf-8")
        # The count chip on the leaderboard module reflects the cap.
        assert "İlk 12" in body
