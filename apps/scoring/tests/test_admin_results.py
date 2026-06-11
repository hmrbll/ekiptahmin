"""Staff result-entry wizard tests — access control, render, save flow."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.predictions.models import SlotPrediction
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
def r16_stage(t):
    return Stage.objects.create(
        tournament=t, kind=Stage.R16, order=2,
        points_exact=9, points_diff=6, points_result=3,
        penalty_loser_pct=Decimal("0.60"),
    )


@pytest.fixture
def tur(t):
    return Team.objects.create(tournament=t, code="TUR", name_tr="Türkiye", group_letter="A")


@pytest.fixture
def bra(t):
    return Team.objects.create(tournament=t, code="BRA", name_tr="Brezilya", group_letter="A")


@pytest.fixture
def group_slot(t, group_stage, tur, bra):
    return BracketSlot.objects.create(
        tournament=t, stage=group_stage, position="GroupA-M1",
        scheduled_kickoff=timezone.now() + timedelta(days=10),
        home_team_actual=tur, away_team_actual=bra,
    )


@pytest.fixture
def r16_slot(t, r16_stage):
    return BracketSlot.objects.create(
        tournament=t, stage=r16_stage, position="R16-1",
        scheduled_kickoff=timezone.now() + timedelta(days=20),
        # No teams yet — admin must pick.
    )


@pytest.fixture
def staff(db):
    u = User.objects.create_user(
        email="staff@x.com", username="staff@x.com", nickname="Staff", is_staff=True,
    )
    return u


@pytest.fixture
def regular(db):
    return User.objects.create_user(
        email="regular@x.com", username="regular@x.com", nickname="Regular",
    )


@pytest.mark.django_db
class TestAccessControl:
    def test_anonymous_redirected_from_entry(self, client):
        r = client.get(reverse("admin_results_entry"))
        # staff_member_required → 302 to admin login
        assert r.status_code == 302
        assert "/admin/login/" in r["Location"] or "/auth/login/" in r["Location"]

    def test_non_staff_redirected(self, client, regular):
        client.force_login(regular)
        r = client.get(reverse("admin_results_entry"))
        assert r.status_code == 302

    def test_staff_can_access(self, client, staff, t):
        client.force_login(staff)
        r = client.get(reverse("admin_results_entry"))
        assert r.status_code == 302
        assert r["Location"].endswith("/group/A/")


@pytest.mark.django_db
class TestGroupStep:
    def test_group_step_renders(self, client, staff, t, group_slot):
        client.force_login(staff)
        r = client.get(reverse("admin_results_group", args=["A"]))
        assert r.status_code == 200
        body = r.content.decode("utf-8")
        assert "GroupA-M1" in body
        # Group rows show team names as locked labels (not selects).
        assert "Türkiye" in body
        assert "Brezilya" in body
        # Step nav present (admin wizard is English).
        assert "Group B" in body  # neighbouring step pill

    def test_unknown_group_redirects_to_entry(self, client, staff, t):
        client.force_login(staff)
        r = client.get(reverse("admin_results_group", args=["Z"]))
        assert r.status_code == 302


@pytest.mark.django_db
class TestKnockoutStep:
    def test_knockout_step_renders_with_team_selects(
        self, client, staff, t, r16_slot, tur, bra,
    ):
        client.force_login(staff)
        r = client.get(reverse("admin_results_knockout", args=["R16"]))
        assert r.status_code == 200
        body = r.content.decode("utf-8")
        assert "R16-1" in body
        # Team selects rendered — both team options appear within form selects.
        assert "Türkiye" in body
        assert "Brezilya" in body
        # Penalty section visible for knockout (admin wizard is English).
        assert "Went to penalties" in body


@pytest.mark.django_db
class TestSaveEndpoint:
    def test_group_save_creates_actual_result_and_recomputes(
        self, client, staff, t, group_slot, group_stage, tur, bra,
    ):
        # A predictor exists so signal/recompute has someone to score.
        predictor = User.objects.create_user(
            email="p@x.com", username="p@x.com", nickname="P",
        )
        pre_round = PredictionRound.objects.create(
            tournament=t, name="Pre", order=0,
            deadline=timezone.now() + timedelta(days=30),
            weight=Decimal("1.00"),
        )
        pre_round.editable_stages.set([group_stage])
        SlotPrediction.objects.create(
            user=predictor, prediction_round=pre_round, slot=group_slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )

        client.force_login(staff)
        r = client.post(
            reverse("admin_results_save", args=[group_slot.id]),
            {
                "home_score": 2, "away_score": 1,
                "went_to_extra_time": "", "went_to_penalties": "",
            },
            HTTP_HX_REQUEST="true",
        )
        assert r.status_code == 200
        actual = ActualResult.objects.filter(slot=group_slot).first()
        assert actual is not None
        assert actual.home_score == 2
        # Predictor's SlotScore should reflect "exact" (6 pts × 1.00).
        score = SlotScore.objects.get(user=predictor, slot=group_slot)
        assert score.matchup_type == "exact"
        assert score.total == Decimal("6")

    def test_knockout_save_assigns_teams_and_score(
        self, client, staff, t, r16_slot, tur, bra,
    ):
        client.force_login(staff)
        r = client.post(
            reverse("admin_results_save", args=[r16_slot.id]),
            {
                "home_team_actual": tur.id,
                "away_team_actual": bra.id,
                "home_score": 1, "away_score": 0,
                "went_to_extra_time": "", "went_to_penalties": "",
            },
            HTTP_HX_REQUEST="true",
        )
        assert r.status_code == 200
        r16_slot.refresh_from_db()
        assert r16_slot.home_team_actual == tur
        assert r16_slot.away_team_actual == bra
        actual = ActualResult.objects.filter(slot=r16_slot).first()
        assert actual is not None
        assert actual.home_score == 1

    def test_invalid_penalty_state_rejected(
        self, client, staff, t, r16_slot, tur, bra,
    ):
        client.force_login(staff)
        r = client.post(
            reverse("admin_results_save", args=[r16_slot.id]),
            {
                "home_team_actual": tur.id,
                "away_team_actual": bra.id,
                # Penalty checked but no winner/scores → invalid.
                "home_score": 1, "away_score": 1,
                "went_to_penalties": "on",
            },
            HTTP_HX_REQUEST="true",
        )
        assert r.status_code == 200  # row re-renders with errors
        # No ActualResult should have been written.
        assert not ActualResult.objects.filter(slot=r16_slot).exists()
