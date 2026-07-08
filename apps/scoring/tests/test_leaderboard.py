"""Leaderboard aggregation + tie-explanation tests + view smoke test."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.predictions.models import SlotPrediction
from apps.scoring.leaderboard import describe_ties, leaderboard_for_tournament
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
def ko_slot(t, pre_round, tur, bra):
    """A knockout slot whose stage the pre_round can edit — for penalty picks."""
    ko_stage = Stage.objects.create(
        tournament=t, kind=Stage.R16, order=1,
        points_exact=6, points_diff=4, points_result=2,
        penalty_loser_pct=Decimal("0.60"),
    )
    pre_round.editable_stages.add(ko_stage)
    return BracketSlot.objects.create(
        tournament=t, stage=ko_stage, position="R16-1",
        scheduled_kickoff=timezone.now() + timedelta(days=12),
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
    def test_anonymous_can_view(self, client, t):
        r = client.get(reverse("leaderboard"))
        assert r.status_code == 200

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
        assert "Puan Durumu".encode("utf-8") in r.content
        assert b"Me" in r.content

    def test_view_renders_empty_state(self, client, t):
        u = User.objects.create_user(email="x@x.com", username="x@x.com", nickname="X")
        client.force_login(u)
        r = client.get(reverse("leaderboard"))
        assert r.status_code == 200
        assert "Henüz puan kaydedilmedi".encode("utf-8") in r.content

    def test_table_links_to_user_detail(
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
        assert reverse("leaderboard_user_detail", args=[u.id]).encode() in r.content


@pytest.mark.django_db
class TestUserDetailView:
    def test_anonymous_can_view(self, client, t):
        u = User.objects.create_user(email="x@x.com", username="x@x.com", nickname="X")
        r = client.get(reverse("leaderboard_user_detail", args=[u.id]))
        assert r.status_code == 200

    def test_pre_lock_predictions_hidden_from_non_owner(
        self, client, t, pre_round, slot1, tur, bra,
    ):
        owner = User.objects.create_user(email="o@x.com", username="o@x.com", nickname="Owner")
        SlotPrediction.objects.create(
            user=owner, prediction_round=pre_round, slot=slot1,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        # No ActualResult and kickoff is in the future → slot not locked and
        # unscored, so it doesn't appear on the score sheet at all.
        r = client.get(reverse("leaderboard_user_detail", args=[owner.id]))
        assert r.status_code == 200
        body = r.content.decode("utf-8")
        # The prediction's score (2–1) must NOT leak to a non-owner viewer.
        assert "2–1" not in body

    def test_renders_user_breakdown_with_score_badges(
        self, client, t, pre_round, slot1, tur, bra,
    ):
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        # Exact prediction on slot1. (The ganyan score sheet only lists slots
        # the user actually predicted+scored — unpredicted slots don't appear.)
        SlotPrediction.objects.create(
            user=u, prediction_round=pre_round, slot=slot1,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        ActualResult.objects.create(slot=slot1, home_score=2, away_score=1)

        client.force_login(u)
        r = client.get(reverse("leaderboard_user_detail", args=[u.id]))
        assert r.status_code == 200
        # Header
        assert b"Me" in r.content
        assert "Toplam".encode("utf-8") in r.content
        # Slot1 should appear with an "exact" badge somewhere.
        assert b"GroupA-M1" in r.content
        assert "Tam skor".encode("utf-8") in r.content

    def test_shows_penalty_pick_under_prediction(
        self, client, t, pre_round, ko_slot, tur, bra,
    ):
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        SlotPrediction.objects.create(
            user=u, prediction_round=pre_round, slot=ko_slot,
            home_team=tur, away_team=bra, home_score=2, away_score=2,
            home_penalties=4, away_penalties=3, penalty_winner=tur,
        )
        ActualResult.objects.create(slot=ko_slot, home_score=1, away_score=0)
        client.force_login(u)
        r = client.get(reverse("leaderboard_user_detail", args=[u.id]))
        body = r.content.decode("utf-8")
        assert "2–2" in body
        assert "pen: TUR 4–3" in body

    def test_renders_empty_state_for_user_with_no_scores(
        self, client, t,
    ):
        u_viewer = User.objects.create_user(email="v@x.com", username="v@x.com", nickname="V")
        u_target = User.objects.create_user(email="t@x.com", username="t@x.com", nickname="T")
        client.force_login(u_viewer)
        r = client.get(reverse("leaderboard_user_detail", args=[u_target.id]))
        assert r.status_code == 200
        assert "puan kaydı yok".encode("utf-8") in r.content

    def test_404_for_unknown_user(self, client, t):
        u = User.objects.create_user(email="x@x.com", username="x@x.com", nickname="X")
        client.force_login(u)
        r = client.get(reverse("leaderboard_user_detail", args=[999999]))
        assert r.status_code == 404


@pytest.mark.django_db
class TestResultsView:
    def test_anonymous_can_view(self, client, t):
        r = client.get(reverse("results"))
        assert r.status_code == 200

    def test_empty_state_when_no_results(self, client, t):
        u = User.objects.create_user(email="x@x.com", username="x@x.com", nickname="X")
        client.force_login(u)
        r = client.get(reverse("results"))
        assert r.status_code == 200
        assert "Henüz sonuç girilmedi".encode("utf-8") in r.content

    def test_lists_played_matches_with_user_breakdown(
        self, client, t, pre_round, slot1, tur, bra,
    ):
        u1 = User.objects.create_user(email="a@x.com", username="a@x.com", nickname="Ali")
        u2 = User.objects.create_user(email="b@x.com", username="b@x.com", nickname="Veli")
        SlotPrediction.objects.create(
            user=u1, prediction_round=pre_round, slot=slot1,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=u2, prediction_round=pre_round, slot=slot1,
            home_team=tur, away_team=bra, home_score=3, away_score=0,
        )
        ActualResult.objects.create(slot=slot1, home_score=2, away_score=1)

        client.force_login(u1)
        r = client.get(reverse("results"))
        assert r.status_code == 200
        # Slot info
        assert b"GroupA-M1" in r.content
        # Both users + their badges appear
        assert b"Ali" in r.content
        assert b"Veli" in r.content
        # Hemre's spec: exact → "Tam skor", result → "Doğru sonuç"
        assert "Tam skor".encode("utf-8") in r.content
        assert "Doğru sonuç".encode("utf-8") in r.content

    def test_shows_user_prediction_under_match(
        self, client, t, pre_round, slot1, tur, bra,
    ):
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        SlotPrediction.objects.create(
            user=u, prediction_round=pre_round, slot=slot1,
            home_team=tur, away_team=bra, home_score=3, away_score=0,
        )
        ActualResult.objects.create(slot=slot1, home_score=2, away_score=1)
        client.force_login(u)
        r = client.get(reverse("results"))
        body = r.content.decode("utf-8")
        # Predicted score 3-0 should appear in the user's row.
        assert "3–0" in body
        # Both team names show twice each (once in match header, once in
        # the user's prediction row).
        assert body.count("Türkiye") >= 2
        assert body.count("Brezilya") >= 2

    def test_shows_penalty_pick_in_user_prediction_row(
        self, client, t, pre_round, ko_slot, tur, bra,
    ):
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        SlotPrediction.objects.create(
            user=u, prediction_round=pre_round, slot=ko_slot,
            home_team=tur, away_team=bra, home_score=1, away_score=1,
            home_penalties=5, away_penalties=4, penalty_winner=tur,
        )
        ActualResult.objects.create(slot=ko_slot, home_score=2, away_score=0)
        client.force_login(u)
        body = client.get(reverse("results")).content.decode("utf-8")
        assert "1–1" in body
        assert "pen: TUR 5–4" in body

    def test_excludes_slots_without_actual_result(
        self, client, t, pre_round, slot1, slot2, tur, bra,
    ):
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        SlotPrediction.objects.create(
            user=u, prediction_round=pre_round, slot=slot2,
            home_team=bra, away_team=tur, home_score=1, away_score=0,
        )
        # Only slot1 has an ActualResult.
        ActualResult.objects.create(slot=slot1, home_score=2, away_score=1)
        client.force_login(u)
        r = client.get(reverse("results"))
        assert b"GroupA-M1" in r.content
        assert b"GroupA-M2" not in r.content


@pytest.mark.django_db
class TestLeaderboardCountColumns:
    def test_columns_show_per_matchup_counts(
        self, client, t, pre_round, slot1, slot2, tur, bra,
    ):
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        # Slot1: exact (TUR 2-1 BRA)
        SlotPrediction.objects.create(
            user=u, prediction_round=pre_round, slot=slot1,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        # Slot2: wrong outcome (predicted home win, actual is away win)
        SlotPrediction.objects.create(
            user=u, prediction_round=pre_round, slot=slot2,
            home_team=bra, away_team=tur, home_score=3, away_score=0,
        )
        ActualResult.objects.create(slot=slot1, home_score=2, away_score=1)
        ActualResult.objects.create(slot=slot2, home_score=0, away_score=2)

        client.force_login(u)
        r = client.get(reverse("leaderboard"))
        body = r.content.decode("utf-8")
        # Header columns appear with Hemre's wording.
        for col in [
            "Toplam Puan", "Doğru Skor", "Doğru Fark", "Doğru Sonuç",
            "Penaltı", "Yanlış",
        ]:
            assert col in body, col

    def test_no_prediction_count_separate_from_wrong(
        self, client, t, pre_round, slot1, slot2, tur, bra,
    ):
        """`no_prediction` (didn't predict) shows in its own column, not folded
        into `wrong` (predicted but missed).
        """
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        # Slot1: prediction is wrong outcome → miss
        SlotPrediction.objects.create(
            user=u, prediction_round=pre_round, slot=slot1,
            home_team=tur, away_team=bra, home_score=0, away_score=2,
        )
        ActualResult.objects.create(slot=slot1, home_score=2, away_score=1)
        # Slot2: no prediction at all → no_prediction (needs explicit recompute
        # since there's no SlotPrediction save signal to trigger it).
        ActualResult.objects.create(slot=slot2, home_score=1, away_score=1)
        from apps.scoring.cache import recompute_slot_for_user
        recompute_slot_for_user(u, slot2)

        client.force_login(u)
        client.get(reverse("leaderboard"))
        # The user's row should contain a "1" in both Yanlış and Tahmin Yapmadı
        # columns. Easiest assertion: the entry's exposed count fields.
        from apps.scoring.leaderboard import leaderboard_for_tournament
        entries = leaderboard_for_tournament(t)
        me = entries[0]
        assert me.counts.get("miss") == 1
        assert me.counts.get("no_prediction") == 1


@pytest.mark.django_db
class TestMatchDetailReveal:
    """The ganyan tablosu (incl. its pre-result pool preview) reveals once the
    slot's prediction round has closed (its deadline passed) — NOT at the match's
    own kickoff — or once the slot is scored. (The home-grid chips are the
    stricter result-only surface; see test_home.)"""

    def _round(self, t, group_stage, deadline):
        rnd = PredictionRound.objects.create(
            tournament=t, name="Pre", order=0, deadline=deadline, weight=Decimal("1.00"),
        )
        rnd.editable_stages.set([group_stage])
        return rnd

    def _slot(self, t, group_stage, kickoff, tur, bra):
        return BracketSlot.objects.create(
            tournament=t, stage=group_stage, position="GroupA-M1",
            scheduled_kickoff=kickoff, home_team_actual=tur, away_team_actual=bra,
        )

    def test_tablosu_shown_once_round_closed_before_kickoff(self, client, t, group_stage, tur, bra):
        rnd = self._round(t, group_stage, timezone.now() - timedelta(hours=1))  # round CLOSED
        slot = self._slot(t, group_stage, timezone.now() + timedelta(days=5), tur, bra)  # kickoff FUTURE
        u = User.objects.create_user(email="u@x.com", username="u@x.com", nickname="U")
        SlotPrediction.objects.create(
            user=u, prediction_round=rnd, slot=slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        body = client.get(reverse("match_detail", args=[slot.id])).content.decode("utf-8")
        # Revealed at round-close even though kickoff is still days away.
        assert "Ganyan Tablosu" in body

    def test_tablosu_hidden_while_round_open_even_after_kickoff(self, client, t, group_stage, tur, bra):
        rnd = self._round(t, group_stage, timezone.now() + timedelta(days=5))  # round OPEN
        slot = self._slot(t, group_stage, timezone.now() - timedelta(hours=2), tur, bra)  # kickoff PASSED
        u = User.objects.create_user(email="u@x.com", username="u@x.com", nickname="U")
        SlotPrediction.objects.create(
            user=u, prediction_round=rnd, slot=slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        body = client.get(reverse("match_detail", args=[slot.id])).content.decode("utf-8")
        # Round still open → hidden, even though the match already kicked off.
        assert "tahmin turu kapanınca" in body
        assert "Ganyan Tablosu" not in body

    def test_tablosu_visible_after_result_even_if_round_open(self, client, t, group_stage, tur, bra):
        rnd = self._round(t, group_stage, timezone.now() + timedelta(days=5))  # round OPEN
        slot = self._slot(t, group_stage, timezone.now() + timedelta(days=5), tur, bra)  # kickoff FUTURE
        u = User.objects.create_user(email="u@x.com", username="u@x.com", nickname="U")
        SlotPrediction.objects.create(
            user=u, prediction_round=rnd, slot=slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        ActualResult.objects.create(slot=slot, home_score=2, away_score=1)
        body = client.get(reverse("match_detail", args=[slot.id])).content.decode("utf-8")
        # A result short-circuits the round gate.
        assert "Ganyan Tablosu" in body

    def test_tablosu_shown_when_stage_pruned_from_all_rounds(self, client, t, group_stage, tur, bra):
        # Closing a stage prunes it from every round's editable_stages. With no
        # round still listing the stage, the pick is final → reveal (even before
        # this match's own kickoff, and with no result).
        rnd = PredictionRound.objects.create(
            tournament=t, name="X", order=0,
            deadline=timezone.now() + timedelta(days=5), weight=Decimal("1.00"),
        )  # an OPEN round, but it does NOT list the group stage
        slot = self._slot(t, group_stage, timezone.now() + timedelta(days=5), tur, bra)
        u = User.objects.create_user(email="u@x.com", username="u@x.com", nickname="U")
        SlotPrediction.objects.create(
            user=u, prediction_round=rnd, slot=slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        body = client.get(reverse("match_detail", args=[slot.id])).content.decode("utf-8")
        assert "Ganyan Tablosu" in body
