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
        # Template comments must not leak as literal text (multi-line {# #}).
        assert "{#" not in body

    def test_unknown_group_redirects_to_entry(self, client, staff, t):
        client.force_login(staff)
        r = client.get(reverse("admin_results_group", args=["Z"]))
        assert r.status_code == 302


@pytest.mark.django_db
class TestKnockoutStep:
    def test_unresolved_knockout_renders_team_picker(
        self, client, staff, t, r16_slot, tur, bra,
    ):
        """A knockout slot the resolver hasn't filled yet shows a team picker."""
        client.force_login(staff)
        r = client.get(reverse("admin_results_knockout", args=["R16"]))
        assert r.status_code == 200
        body = r.content.decode("utf-8")
        assert "R16-1" in body
        # Team selects rendered — both team options appear within form selects.
        assert 'name="home_team_actual"' in body
        assert "Türkiye" in body
        assert "Brezilya" in body
        # ET/penalties are derived from the score shape — no manual toggles,
        # and no beyond-90' section until a draw is entered.
        assert "Went to extra time" not in body
        assert "Went to penalties" not in body
        assert 'name="home_score_aet"' not in body
        assert "{#" not in body  # no leaked template comments

    def test_resolved_knockout_shows_fixed_teams_no_picker(
        self, client, staff, t, r16_stage, tur, bra,
    ):
        """Once teams are resolved, the row shows them as fixed flag labels —
        no dropdown to pick from."""
        BracketSlot.objects.create(
            tournament=t, stage=r16_stage, position="R16-1",
            scheduled_kickoff=timezone.now() + timedelta(days=20),
            home_team_actual=tur, away_team_actual=bra,
        )
        client.force_login(staff)
        r = client.get(reverse("admin_results_knockout", args=["R16"]))
        body = r.content.decode("utf-8")
        assert "R16-1" in body
        assert "Türkiye" in body
        assert "Brezilya" in body
        # No team picker — teams are fixed.
        assert 'name="home_team_actual"' not in body
        assert 'name="away_team_actual"' not in body


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

    def test_knockout_draw_requires_extra_time_score(
        self, client, staff, t, r16_slot, tur, bra,
    ):
        """A level 90' knockout score means extra time was played — the 120'
        score is required before anything is written. The rejected row reveals
        the ET inputs (but not yet the shootout — 120' outcome unknown)."""
        client.force_login(staff)
        r = client.post(
            reverse("admin_results_save", args=[r16_slot.id]),
            {
                "home_team_actual": tur.id,
                "away_team_actual": bra.id,
                # Drawn 90' score, no 120' score → invalid.
                "home_score": 1, "away_score": 1,
            },
            HTTP_HX_REQUEST="true",
        )
        assert r.status_code == 200  # row re-renders with errors
        body = r.content.decode("utf-8")
        assert "extra time" in body.lower()
        assert 'name="home_score_aet"' in body
        assert "decided on penalties" not in body.lower()
        # No ActualResult should have been written.
        assert not ActualResult.objects.filter(slot=r16_slot).exists()

    def test_knockout_et_draw_requires_penalties(
        self, client, staff, t, r16_slot, tur, bra,
    ):
        """Still level after 120' → a shootout happened; winner + score are
        required. The rejected row reveals the penalty fields."""
        client.force_login(staff)
        r = client.post(
            reverse("admin_results_save", args=[r16_slot.id]),
            {
                "home_team_actual": tur.id,
                "away_team_actual": bra.id,
                "home_score": 1, "away_score": 1,
                "home_score_aet": 1, "away_score_aet": 1,
            },
            HTTP_HX_REQUEST="true",
        )
        assert r.status_code == 200
        assert "decided on penalties" in r.content.decode("utf-8").lower()
        assert not ActualResult.objects.filter(slot=r16_slot).exists()

    def test_knockout_decided_in_extra_time(
        self, client, staff, t, r16_slot, tur, bra,
    ):
        """90' draw + decisive 120' score saves as an ET win: no penalties,
        stray shootout fields cleared, ET flag derived True."""
        client.force_login(staff)
        r = client.post(
            reverse("admin_results_save", args=[r16_slot.id]),
            {
                "home_team_actual": tur.id,
                "away_team_actual": bra.id,
                "home_score": 2, "away_score": 2,
                "home_score_aet": 3, "away_score_aet": 2,
                # Stray shootout junk must be cleared on an ET-decided match.
                "penalty_winner": bra.id, "home_penalties": 4, "away_penalties": 2,
            },
            HTTP_HX_REQUEST="true",
        )
        assert r.status_code == 200
        actual = ActualResult.objects.get(slot=r16_slot)
        assert actual.went_to_extra_time is True
        assert actual.went_to_penalties is False
        assert (actual.home_score_aet, actual.away_score_aet) == (3, 2)
        assert actual.penalty_winner is None
        assert actual.home_penalties is None
        assert (actual.effective_home_score, actual.effective_away_score) == (3, 2)

    def test_knockout_aet_lower_than_90_rejected(
        self, client, staff, t, r16_slot, tur, bra,
    ):
        """The 120' score can only add goals to the 90' score."""
        client.force_login(staff)
        r = client.post(
            reverse("admin_results_save", args=[r16_slot.id]),
            {
                "home_team_actual": tur.id,
                "away_team_actual": bra.id,
                "home_score": 2, "away_score": 2,
                "home_score_aet": 1, "away_score_aet": 2,
            },
            HTTP_HX_REQUEST="true",
        )
        assert r.status_code == 200
        assert not ActualResult.objects.filter(slot=r16_slot).exists()

    def test_knockout_draw_auto_derives_penalties(
        self, client, staff, t, r16_slot, tur, bra,
    ):
        """A knockout level through 120' with a shootout winner saves with
        went_to_penalties derived True — no manual flag."""
        client.force_login(staff)
        r = client.post(
            reverse("admin_results_save", args=[r16_slot.id]),
            {
                "home_team_actual": tur.id,
                "away_team_actual": bra.id,
                "home_score": 1, "away_score": 1,
                "home_score_aet": 1, "away_score_aet": 1,
                "penalty_winner": tur.id,
                "home_penalties": 4, "away_penalties": 2,
            },
            HTTP_HX_REQUEST="true",
        )
        assert r.status_code == 200
        actual = ActualResult.objects.get(slot=r16_slot)
        assert actual.went_to_extra_time is True
        assert actual.went_to_penalties is True
        assert actual.penalty_winner == tur
        assert actual.home_penalties == 4

    def test_decisive_knockout_clears_penalties(
        self, client, staff, t, r16_slot, tur, bra,
    ):
        """A decisive 90' knockout score ends at regulation, even if stray
        beyond-90' fields are posted."""
        client.force_login(staff)
        r = client.post(
            reverse("admin_results_save", args=[r16_slot.id]),
            {
                "home_team_actual": tur.id,
                "away_team_actual": bra.id,
                "home_score": 2, "away_score": 1,
                "home_score_aet": 3, "away_score_aet": 1,
                "penalty_winner": tur.id, "home_penalties": 4, "away_penalties": 2,
            },
            HTTP_HX_REQUEST="true",
        )
        assert r.status_code == 200
        actual = ActualResult.objects.get(slot=r16_slot)
        assert actual.went_to_extra_time is False
        assert actual.went_to_penalties is False
        assert actual.home_score_aet is None
        assert actual.penalty_winner is None
        assert actual.home_penalties is None

    def test_wizard_save_stamps_manual_source_over_api_row(
        self, client, staff, t, group_slot, tur, bra,
    ):
        """Editing a live-synced (API) row through the wizard flips it to
        MANUAL — from then on the poller must leave it alone."""
        ActualResult.objects.create(
            slot=group_slot, home_score=0, away_score=0,
            source=ActualResult.SOURCE_API,
        )
        client.force_login(staff)
        r = client.post(
            reverse("admin_results_save", args=[group_slot.id]),
            {"home_score": 2, "away_score": 1},
            HTTP_HX_REQUEST="true",
        )
        assert r.status_code == 200
        actual = ActualResult.objects.get(slot=group_slot)
        assert (actual.home_score, actual.away_score) == (2, 1)
        assert actual.source == ActualResult.SOURCE_MANUAL

    def test_et_decided_row_renders_without_penalty_demand(
        self, client, staff, t, r16_stage, tur, bra,
    ):
        """A saved ET-decided result (90' draw, decisive 120') renders its ET
        score — and must NOT claim the match was decided on penalties."""
        slot = BracketSlot.objects.create(
            tournament=t, stage=r16_stage, position="R16-2",
            scheduled_kickoff=timezone.now() + timedelta(days=20),
            home_team_actual=tur, away_team_actual=bra,
        )
        ActualResult.objects.create(
            slot=slot, home_score=2, away_score=2,
            home_score_aet=3, away_score_aet=2,
            went_to_extra_time=True, source=ActualResult.SOURCE_API,
        )
        client.force_login(staff)
        r = client.get(reverse("admin_results_knockout", args=["R16"]))
        body = r.content.decode("utf-8")
        assert 'name="home_score_aet"' in body
        assert 'value="3"' in body  # stored 120' score prefilled
        assert "decided on penalties" not in body.lower()
