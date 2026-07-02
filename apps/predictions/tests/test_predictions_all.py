"""Tests for /predictions/all/ — public match-by-match predictions page."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.predictions.models import SlotPrediction
from apps.scoring.models import GanyanScore
from apps.tournament.models import ActualResult, BracketSlot, PredictionRound

User = get_user_model()


def _past_slot(tournament, stage_group, team_tur, team_bra, position="GroupA-M2"):
    return BracketSlot.objects.create(
        tournament=tournament, stage=stage_group, position=position,
        scheduled_kickoff=timezone.now() - timedelta(hours=2),
        home_team_actual=team_tur, away_team_actual=team_bra,
    )


@pytest.mark.django_db
class TestPredictionsAll:
    def test_anonymous_can_view(self, client, tournament):
        r = client.get(reverse("predictions_all"))
        assert r.status_code == 200
        assert "Tüm Tahminler".encode("utf-8") in r.content

    def test_pre_lock_match_hides_predictions_but_shows_count(
        self, client, tournament, prediction_round, group_slot, team_tur, team_bra,
    ):
        u1 = User.objects.create_user(email="a@x.com", username="a@x.com", nickname="A")
        u2 = User.objects.create_user(email="b@x.com", username="b@x.com", nickname="B")
        SlotPrediction.objects.create(
            user=u1, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=u2, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=3, away_score=0,
        )
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        # Match itself appears
        assert "GroupA-M1" in body
        # Count is shown
        assert "2 oyuncu tahmin etti" in body
        # But the actual predicted scores must not leak.
        assert "2–1" not in body
        assert "3–0" not in body

    def test_locked_match_reveals_predictions(
        self, client, tournament, stage_group, prediction_round, team_tur, team_bra,
    ):
        # Use a past kickoff so the slot is locked.
        past = _past_slot(tournament, stage_group, team_tur, team_bra, "GroupA-M2")
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        SlotPrediction.objects.create(
            user=u, prediction_round=prediction_round, slot=past,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        assert "GroupA-M2" in body
        assert "Me" in body
        assert "2–1" in body  # prediction revealed

    def test_match_with_result_reveals_predictions_even_pre_kickoff(
        self, client, tournament, prediction_round, group_slot, team_tur, team_bra,
    ):
        """Admin entering a result early (test mode) should reveal predictions
        even when scheduled_kickoff is still in the future.
        """
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        SlotPrediction.objects.create(
            user=u, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        ActualResult.objects.create(slot=group_slot, home_score=2, away_score=1)
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        assert "GroupA-M1" in body
        assert "2–1" in body

    def test_predictions_revealed_after_submission_deadline_pre_kickoff(
        self, client, tournament, stage_group, group_slot, team_tur, team_bra,
    ):
        """Predictions go public once the submission deadline passes, even
        while the match itself is still in the future (not yet kicked off)."""
        pr = PredictionRound.objects.create(
            tournament=tournament, name="Pre", order=0,
            deadline=timezone.now() - timedelta(hours=1), weight=Decimal("1.00"),
        )
        pr.editable_stages.set([stage_group])
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        SlotPrediction.objects.create(
            user=u, prediction_round=pr, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        assert "GroupA-M1" in body
        assert "2–1" in body  # revealed at deadline, before kickoff

    def test_predictions_revealed_when_no_round_can_edit_the_stage(
        self, client, tournament, stage_group, stage_r16, group_slot, team_tur, team_bra,
    ):
        """Admins close a stage by removing it from every round's
        editable_stages. Once no open round can edit the slot's stage, its
        predictions reveal — even if the round's own deadline is still future
        and the match hasn't kicked off."""
        pr = PredictionRound.objects.create(
            tournament=tournament, name="Knockouts only", order=0,
            deadline=timezone.now() + timedelta(days=10), weight=Decimal("1.00"),
        )
        pr.editable_stages.set([stage_r16])  # GROUP intentionally not editable
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        SlotPrediction.objects.create(
            user=u, prediction_round=pr, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        assert "GroupA-M1" in body
        assert "2–1" in body

    def test_predictions_hidden_while_a_later_round_can_still_edit(
        self, client, tournament, stage_group, group_slot, team_tur, team_bra,
    ):
        """If any open round can still edit the slot's stage, predictions stay
        hidden — even after an earlier round's deadline has passed."""
        early = PredictionRound.objects.create(
            tournament=tournament, name="Early", order=0,
            deadline=timezone.now() - timedelta(hours=1), weight=Decimal("1.00"),
        )
        early.editable_stages.set([stage_group])
        late = PredictionRound.objects.create(
            tournament=tournament, name="Late", order=1,
            deadline=timezone.now() + timedelta(days=2), weight=Decimal("1.00"),
        )
        late.editable_stages.set([stage_group])
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        SlotPrediction.objects.create(
            user=u, prediction_round=early, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        assert "GroupA-M1" in body
        assert "2–1" not in body
        assert "1 oyuncu tahmin etti" in body

    def test_scored_match_shows_earned_points(
        self, client, tournament, stage_group, prediction_round, group_slot, team_tur, team_bra,
    ):
        """Once a result is entered, each predictor's earned ganyan points show
        next to their prediction."""
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        SlotPrediction.objects.create(
            user=u, prediction_round=prediction_round, slot=group_slot,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        ActualResult.objects.create(slot=group_slot, home_score=2, away_score=1)
        # Pin a known payout regardless of whatever the recompute signal wrote.
        GanyanScore.objects.update_or_create(
            user=u, slot=group_slot,
            defaults={"total": Decimal("7.50"), "outcome": GanyanScore.EXACT},
        )
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        assert "7,50" in body  # tr locale -> comma decimal separator
        assert "puan" in body
        # Scored matches show earned points, never the pre-result "en fazla" hint.
        assert "en fazla" not in body

    def test_unscored_revealed_match_shows_potential_points(
        self, client, tournament, stage_group, prediction_round, team_tur, team_bra,
    ):
        """A revealed-but-unplayed match (locked, no result) shows, next to each
        complete pick, the most it could earn if it lands exactly. Sole predictor
        with default 100/100/100 pools and weight 1.0 → exact+diff+result = 300."""
        past = _past_slot(tournament, stage_group, team_tur, team_bra, "GroupA-M2")
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Me")
        SlotPrediction.objects.create(
            user=u, prediction_round=prediction_round, slot=past,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        assert "GroupA-M2" in body
        assert "en fazla" in body
        assert "300,00" in body

    def test_potential_splits_among_cowinners(
        self, client, tournament, stage_group, prediction_round, team_tur, team_bra,
    ):
        """Two identical picks split each pool: the best case halves to 150,
        not the 300 a sole winner would take — proving parimutuel division."""
        past = _past_slot(tournament, stage_group, team_tur, team_bra, "GroupA-M2")
        for i in range(2):
            ui = User.objects.create_user(
                email=f"u{i}@x.com", username=f"u{i}@x.com", nickname=f"U{i}",
            )
            SlotPrediction.objects.create(
                user=ui, prediction_round=prediction_round, slot=past,
                home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
            )
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        assert body.count("en fazla") == 2
        assert "150,00" in body
        assert "300,00" not in body

    def test_unscored_wrong_matchup_pick_excluded(
        self, client, tournament, stage_r16, prediction_round, team_tur, team_bra, team_arg,
    ):
        """On a resolved knockout slot, a pick whose matchup doesn't line up with
        the real fixture (made during the bracket-forecast phase) is excluded
        from the match card entirely — it can never score this fixture, so
        listing it under the real teams would be misleading. The card instead
        notes that the slot was predicted but nobody hit the matchup."""
        past = BracketSlot.objects.create(
            tournament=tournament, stage=stage_r16, position="R16-1",
            scheduled_kickoff=timezone.now() - timedelta(hours=2),
            home_team_actual=team_tur, away_team_actual=team_bra,
        )
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Zlatan")
        # Predicted away side ARG, but the real fixture resolved to BRA.
        SlotPrediction.objects.create(
            user=u, prediction_round=prediction_round, slot=past,
            home_team=team_tur, away_team=team_arg, home_score=2, away_score=1,
        )
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        assert "R16-1" in body
        # The wrong-matchup pick is not listed under the real fixture.
        match = next(
            m for sec in r.context["sections"] for m in sec["matches"]
            if m["slot"].position == "R16-1"
        )
        assert match["predictions"] == []
        assert "Zlatan" not in body
        assert "en fazla" not in body
        # ...but the card acknowledges the slot was predicted (wrong matchup).
        assert "kimse bu eşleşmeyi tutturamadı" in body

    def test_multi_round_picks_show_each_with_its_weight(
        self, client, tournament, stage_r16, team_tur, team_bra,
    ):
        """A user who predicted the same fixture in two rounds gets one row per
        round — earliest first, each tagged with its round weight and its own
        best-case payout."""
        past = BracketSlot.objects.create(
            tournament=tournament, stage=stage_r16, position="R16-1",
            scheduled_kickoff=timezone.now() - timedelta(hours=2),
            home_team_actual=team_tur, away_team_actual=team_bra,
        )
        r0 = PredictionRound.objects.create(
            tournament=tournament, name="Pre", order=0,
            deadline=timezone.now() - timedelta(hours=1), weight=Decimal("1.00"),
        )
        r0.editable_stages.set([stage_r16])
        r1 = PredictionRound.objects.create(
            tournament=tournament, name="Grup sonrası", order=1,
            deadline=timezone.now() - timedelta(hours=1), weight=Decimal("0.85"),
        )
        r1.editable_stages.set([stage_r16])
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Solo")
        SlotPrediction.objects.create(
            user=u, prediction_round=r0, slot=past,
            home_team=team_tur, away_team=team_bra, home_score=1, away_score=0,
        )
        SlotPrediction.objects.create(
            user=u, prediction_round=r1, slot=past,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        match = next(
            m for sec in r.context["sections"] for m in sec["matches"]
            if m["slot"].position == "R16-1"
        )
        rows = match["predictions"]
        assert [p.prediction_round.order for p in rows] == [0, 1]  # earliest first
        by_round = {p.prediction_round.order: p for p in rows}
        # Sole predictor → full pools; round weight scales the best case.
        assert by_round[0].potential_points == Decimal("300.00")  # 100*3 * 1.00
        assert by_round[1].potential_points == Decimal("255.00")  # 100*3 * 0.85
        # Weight badges + per-row best cases rendered.
        assert "(1,00x)" in body
        assert "(0,85x)" in body
        assert "300,00" in body
        assert "255,00" in body

    def test_scored_multi_round_points_on_effective_round_only(
        self, client, tournament, stage_group, stage_r16, team_tur, team_bra,
    ):
        """When the fixture is scored, the earned points sit on the engine's
        effective round; the user's other round shows its pick + weight with an
        explicit 0 (it didn't count, but a blank reads as broken)."""
        past = BracketSlot.objects.create(
            tournament=tournament, stage=stage_r16, position="R16-1",
            scheduled_kickoff=timezone.now() - timedelta(hours=2),
            home_team_actual=team_tur, away_team_actual=team_bra,
        )
        r0 = PredictionRound.objects.create(
            tournament=tournament, name="Pre", order=0,
            deadline=timezone.now() - timedelta(hours=1), weight=Decimal("1.00"),
        )
        r0.editable_stages.set([stage_r16])
        r1 = PredictionRound.objects.create(
            tournament=tournament, name="Grup sonrası", order=1,
            deadline=timezone.now() - timedelta(hours=1), weight=Decimal("0.85"),
        )
        r1.editable_stages.set([stage_r16])
        u = User.objects.create_user(email="me@x.com", username="me@x.com", nickname="Solo")
        SlotPrediction.objects.create(
            user=u, prediction_round=r0, slot=past,
            home_team=team_tur, away_team=team_bra, home_score=1, away_score=0,
        )
        SlotPrediction.objects.create(
            user=u, prediction_round=r1, slot=past,
            home_team=team_tur, away_team=team_bra, home_score=2, away_score=1,
        )
        ActualResult.objects.create(slot=past, home_score=2, away_score=1)
        # Pin a known payout on the round-1 pick regardless of the recompute.
        GanyanScore.objects.update_or_create(
            user=u, slot=past,
            defaults={"total": Decimal("12.75"), "outcome": GanyanScore.EXACT,
                      "effective_round": r1},
        )
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        match = next(
            m for sec in r.context["sections"] for m in sec["matches"]
            if m["slot"].position == "R16-1"
        )
        by_round = {p.prediction_round.order: p for p in match["predictions"]}
        assert by_round[1].earned_points == Decimal("12.75")  # effective round earns
        assert by_round[0].earned_points == 0                 # other round didn't count → explicit 0
        assert "12,75" in body
        assert "(1,00x)" in body
        assert "(0,85x)" in body

    def test_skips_slots_without_resolved_teams(
        self, client, tournament, prediction_round, r16_slot, group_slot,
    ):
        # r16_slot has no home_team_actual / away_team_actual yet.
        r = client.get(reverse("predictions_all"))
        body = r.content.decode("utf-8")
        # Group slot still appears (teams set); R16 doesn't.
        assert "GroupA-M1" in body
        assert "R16-1" not in body


@pytest.mark.django_db
class TestPredictionsAllTabs:
    """Round tabs: group matchdays (İlk/İkinci/Üçüncü) + knockout stages."""

    def _group_slot(self, tournament, stage_group, team_tur, team_bra, match_no, *, kickoff):
        return BracketSlot.objects.create(
            tournament=tournament, stage=stage_group, position=f"GroupA-M{match_no}",
            scheduled_kickoff=kickoff, home_team_actual=team_tur, away_team_actual=team_bra,
        )

    def test_group_matchdays_become_separate_tabs(
        self, client, tournament, stage_group, team_tur, team_bra,
    ):
        now = timezone.now()
        # M1 → matchday 1, M3 → matchday 2, M5 → matchday 3.
        self._group_slot(tournament, stage_group, team_tur, team_bra, 1, kickoff=now + timedelta(days=1))
        self._group_slot(tournament, stage_group, team_tur, team_bra, 3, kickoff=now + timedelta(days=5))
        self._group_slot(tournament, stage_group, team_tur, team_bra, 5, kickoff=now + timedelta(days=9))
        r = client.get(reverse("predictions_all"))
        keys = [s["key"] for s in r.context["sections"]]
        labels = [s["label"] for s in r.context["sections"]]
        assert keys == ["group-md1", "group-md2", "group-md3"]
        assert labels == ["Grup İlk Maçlar", "Grup İkinci Maçlar", "Grup Üçüncü Maçlar"]
        body = r.content.decode("utf-8")
        assert "Grup İlk Maçlar" in body
        assert "Grup Üçüncü Maçlar" in body

    def test_knockout_stage_is_its_own_tab_after_groups(
        self, client, tournament, stage_group, stage_r16, team_tur, team_bra,
    ):
        now = timezone.now()
        self._group_slot(tournament, stage_group, team_tur, team_bra, 1, kickoff=now + timedelta(days=1))
        BracketSlot.objects.create(
            tournament=tournament, stage=stage_r16, position="R16-1",
            scheduled_kickoff=now + timedelta(days=20),
            home_team_actual=team_tur, away_team_actual=team_bra,
        )
        r = client.get(reverse("predictions_all"))
        sections = {s["key"]: s["label"] for s in r.context["sections"]}
        keys = [s["key"] for s in r.context["sections"]]
        assert keys == ["group-md1", "ko-R16"]  # groups before knockout
        assert sections["ko-R16"] == "Son 16"

    def test_default_tab_is_earliest_round_with_an_unplayed_match(
        self, client, tournament, stage_group, team_tur, team_bra,
    ):
        now = timezone.now()
        md1 = self._group_slot(tournament, stage_group, team_tur, team_bra, 1, kickoff=now - timedelta(days=3))
        self._group_slot(tournament, stage_group, team_tur, team_bra, 3, kickoff=now + timedelta(days=2))
        # Matchday 1 fully played → default should advance to matchday 2.
        ActualResult.objects.create(slot=md1, home_score=1, away_score=0)
        r = client.get(reverse("predictions_all"))
        assert r.context["default_section_key"] == "group-md2"

    def test_default_tab_falls_back_to_first_when_all_unplayed(
        self, client, tournament, stage_group, team_tur, team_bra,
    ):
        now = timezone.now()
        self._group_slot(tournament, stage_group, team_tur, team_bra, 1, kickoff=now + timedelta(days=1))
        self._group_slot(tournament, stage_group, team_tur, team_bra, 3, kickoff=now + timedelta(days=5))
        r = client.get(reverse("predictions_all"))
        assert r.context["default_section_key"] == "group-md1"
