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
    def test_renders_dashboard_modules_for_guest(self, client, t):
        """Anonymous visitors see the same three-module overview as
        authenticated users — just without the personal greeting.
        """
        r = client.get(reverse("home"))
        assert r.status_code == 200
        body = r.content.decode("utf-8")
        # Module headers
        assert "Sıradaki maçlar" in body
        assert "Son sonuçlar" in body
        assert "Puan durumu" in body
        # No personal greeting for guests
        assert "Merhaba" not in body
        # Header offers the login pill
        assert "Giriş yap" in body

    def test_guest_does_not_see_per_row_personal_lines(
        self, client, t, group_stage, pre_round, tur, bra,
    ):
        """The "Tahminin: ..." and "Aldığın: ..." lines are only meaningful
        for an identified viewer — guests shouldn't see them at all.
        """
        now = timezone.now()
        slot = _slot(t, group_stage, "GroupA-M1", now + timedelta(days=1), tur, bra)
        # Some other user has predicted, but no one is logged in.
        rival = User.objects.create_user(
            email="r@x.com", username="r@x.com", nickname="R",
        )
        SlotPrediction.objects.create(
            user=rival, prediction_round=pre_round, slot=slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        r = client.get(reverse("home"))
        body = r.content.decode("utf-8")
        assert "GroupA-M1" in body  # the match still appears in upcoming
        assert "Tahminin" not in body
        assert "Aldığın" not in body
        assert "tahmin yok" not in body


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

    def test_upcoming_excludes_slots_with_actual_result(
        self, client, t, group_stage, tur, bra,
    ):
        """Even if kickoff is in the future, a slot that already has an
        ActualResult is treated as done and dropped from the upcoming list.
        """
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        now = timezone.now()
        future_with_result = _slot(
            t, group_stage, "GroupA-M1", now + timedelta(days=2), tur, bra,
        )
        ActualResult.objects.create(slot=future_with_result, home_score=2, away_score=1)
        _slot(
            t, group_stage, "GroupA-M2", now + timedelta(days=3), bra, tur,
        )

        client.force_login(u)
        r = client.get(reverse("home"))
        body = r.content.decode("utf-8")
        # The clean future slot remains in upcoming; the one with an actual
        # result is dropped even though its kickoff is later.
        # `GroupA-M1` will still appear in the recent-results module, so we
        # assert via the "Tahminin"/"— tahmin yok" line which only renders
        # under the upcoming module.
        upcoming_section = body.split("Son sonuçlar")[0]
        assert "GroupA-M2" in upcoming_section
        assert "GroupA-M1" not in upcoming_section

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
        # Ganyan: sole predictor hits exact → exact+diff+result pools all pay
        # the full 100 each = 300.00 (tr locale renders the comma).
        assert "Aldığın" in body
        assert "300,00" in body

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
        assert "border-primary/20" in body

    def test_chips_sort_along_home_to_away_spectrum(
        self, client, t, group_stage, pre_round, tur, bra,
    ):
        """Predicted scores order as 4-1, 3-0, 2-2, 1-1, 0-1, 1-2 — i.e. from
        biggest home margin (with bigger gf first) through draws to growing
        away margins. Chips are post-lock only, so this uses a played match.
        """
        slot = _slot(t, group_stage, "GroupA-M1",
                     timezone.now() - timedelta(days=1), tur, bra)
        score_to_nick = [
            ((4, 1), "U41"),
            ((3, 0), "U30"),
            ((2, 2), "U22"),
            ((1, 1), "U11"),
            ((0, 1), "U01"),
            ((1, 2), "U12"),
        ]
        for (h, a), nick in score_to_nick:
            u = User.objects.create_user(
                email=f"{nick}@x.com", username=f"{nick}@x.com", nickname=nick,
            )
            SlotPrediction.objects.create(
                user=u, prediction_round=pre_round, slot=slot,
                home_team=tur, away_team=bra, home_score=h, away_score=a,
            )
        ActualResult.objects.create(slot=slot, home_score=1, away_score=1)

        viewer = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        client.force_login(viewer)
        r = client.get(reverse("home"))
        body = r.content.decode("utf-8")
        positions = [body.find(nick) for _, nick in score_to_nick]
        # All nicks present and in the expected spectrum order.
        assert all(p > 0 for p in positions), positions
        assert positions == sorted(positions), positions

    def test_played_match_lists_all_user_prediction_chips(
        self, client, t, group_stage, pre_round, tur, bra,
    ):
        """A played (post-lock) match's chip list shows a chip for every user
        who predicted it, including users other than the viewer. (Pre-lock
        matches hide others' predictions — see test_guest_does_not_see_*.)
        """
        me = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        rival = User.objects.create_user(email="r@x.com", username="r@x.com", nickname="Rival")
        slot = _slot(t, group_stage, "GroupA-M1",
                     timezone.now() - timedelta(days=1), tur, bra)
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
        assert "Me" in body and "Rival" in body
        # Both predicted scores show as chips (verbatim with en-dash).
        assert "2–1" in body and "3–0" in body

    def test_recent_match_chips_carry_matchup_colour_classes(
        self, client, t, group_stage, pre_round, tur, bra,
    ):
        """A played match's chip list uses the per-matchup colour classes so
        outcomes are readable at a glance. Palette: emerald = tam skor,
        amber = aynı fark, indigo = doğru sonuç.
        """
        me = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        diff_buddy = User.objects.create_user(email="d@x.com", username="d@x.com", nickname="Diff")
        result_buddy = User.objects.create_user(email="r@x.com", username="r@x.com", nickname="Result")
        slot = _slot(t, group_stage, "GroupA-M1",
                     timezone.now() - timedelta(hours=2), tur, bra)
        # actual = 2–1. Me hits exact, Diff hits same goal-diff (3-2),
        # Result gets correct outcome only (3-0 = result tier).
        SlotPrediction.objects.create(
            user=me, prediction_round=pre_round, slot=slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=diff_buddy, prediction_round=pre_round, slot=slot,
            home_team=tur, away_team=bra, home_score=3, away_score=2,
        )
        SlotPrediction.objects.create(
            user=result_buddy, prediction_round=pre_round, slot=slot,
            home_team=tur, away_team=bra, home_score=3, away_score=0,
        )
        ActualResult.objects.create(slot=slot, home_score=2, away_score=1)
        client.force_login(me)
        r = client.get(reverse("home"))
        body = r.content.decode("utf-8")
        # Distinctive Tailwind class fragments per matchup colour.
        assert "border-primary/30" in body  # me's exact chip
        assert "border-success/30" in body  # diff chip
        assert "border-warning/30" in body  # result chip

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
