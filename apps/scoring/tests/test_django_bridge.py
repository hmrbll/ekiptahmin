"""Tests for the Django→engine bridge and the SlotScore cache.

Covers:
- `score_slot_for_user` correctly assembles engine inputs and returns the
  expected breakdown.
- `recompute_slot_for_*` upserts the right SlotScore rows.
- Signal-driven invalidation (ActualResult + SlotPrediction writes).
- The "no actual result yet" path stores a NO_RESULT row.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.predictions.models import SlotPrediction
from apps.scoring.cache import (
    recompute_slot_for_all_users,
    recompute_slot_for_user,
    recompute_user_all_slots,
)
from apps.scoring.django_bridge import score_slot_for_user
from apps.scoring.models import SlotScore
from apps.tournament.models import (
    ActualResult,
    BracketSlot,
    PredictionRound,
    Stage,
    Team,
    Tournament,
)


# ---------- DB fixtures (self-contained — avoids name clashes with
# apps/scoring/tests/conftest.py which defines pure-Python dataclass fixtures
# under the same labels). ----------


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
def pre_round(t, group_stage, r16_stage):
    pr = PredictionRound.objects.create(
        tournament=t, name="Pre-tournament", order=0,
        deadline=timezone.now() + timedelta(days=30),
        weight=Decimal("1.00"),
    )
    pr.editable_stages.set([group_stage, r16_stage])
    return pr


@pytest.fixture
def after_group_round(t, r16_stage, group_stage):
    pr = PredictionRound.objects.create(
        tournament=t, name="After Group", order=1,
        deadline=timezone.now() + timedelta(days=40),
        weight=Decimal("0.85"),
        depends_on_stage=group_stage,
    )
    pr.editable_stages.set([r16_stage])
    return pr


@pytest.fixture
def tur(t):
    return Team.objects.create(tournament=t, code="TUR", name_tr="Türkiye", group_letter="A")


@pytest.fixture
def bra(t):
    return Team.objects.create(tournament=t, code="BRA", name_tr="Brezilya", group_letter="A")


@pytest.fixture
def arg(t):
    return Team.objects.create(tournament=t, code="ARG", name_tr="Arjantin", group_letter="B")


@pytest.fixture
def ger(t):
    return Team.objects.create(tournament=t, code="GER", name_tr="Almanya", group_letter="B")


@pytest.fixture
def group_slot(t, group_stage, tur, bra):
    return BracketSlot.objects.create(
        tournament=t, stage=group_stage, position="GroupA-M1",
        scheduled_kickoff=timezone.now() + timedelta(days=10),
        home_team_actual=tur, away_team_actual=bra,
    )


@pytest.fixture
def r16_slot(t, r16_stage, tur, arg):
    return BracketSlot.objects.create(
        tournament=t, stage=r16_stage, position="R16-1",
        scheduled_kickoff=timezone.now() + timedelta(days=20),
        home_team_actual=tur, away_team_actual=arg,
    )


@pytest.fixture
def player(db):
    return get_user_model().objects.create_user(
        email="p@example.com", username="p@example.com", nickname="P",
    )


# ---------- Bridge tests ----------


@pytest.mark.django_db
class TestScoreSlotForUser:
    def test_returns_none_when_no_actual_result(
        self, player, pre_round, group_slot, tur, bra,
    ):
        SlotPrediction.objects.create(
            user=player, prediction_round=pre_round, slot=group_slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        assert score_slot_for_user(player, group_slot) is None

    def test_exact_score_pre_round_full_weight(
        self, player, pre_round, group_slot, tur, bra,
    ):
        SlotPrediction.objects.create(
            user=player, prediction_round=pre_round, slot=group_slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        ActualResult.objects.create(slot=group_slot, home_score=2, away_score=1)
        breakdown = score_slot_for_user(player, group_slot)
        assert breakdown.matchup_type == "exact"
        assert breakdown.points_match == Decimal("6")  # GROUP exact × 1.00
        assert breakdown.earning_round_order == 0

    def test_only_correct_matchup_round_wins(
        self, player, pre_round, after_group_round, r16_slot, tur, arg,
    ):
        # Wrong matchup in pre-round, correct in after-group — only the
        # after-group prediction is a candidate, so it earns at weight 0.85.
        wrong = Team.objects.create(tournament=r16_slot.tournament, code="XYZ", name_tr="X")
        SlotPrediction.objects.create(
            user=player, prediction_round=pre_round, slot=r16_slot,
            home_team=tur, away_team=wrong, home_score=1, away_score=0,
        )
        SlotPrediction.objects.create(
            user=player, prediction_round=after_group_round, slot=r16_slot,
            home_team=tur, away_team=arg, home_score=2, away_score=0,
        )
        ActualResult.objects.create(slot=r16_slot, home_score=2, away_score=0)
        breakdown = score_slot_for_user(player, r16_slot)
        assert breakdown.matchup_type == "exact"
        assert breakdown.earning_round_order == 1
        assert breakdown.points_match == Decimal("9") * Decimal("0.85")

    def test_no_prediction_returns_no_prediction(
        self, player, group_slot, tur, bra,
    ):
        ActualResult.objects.create(slot=group_slot, home_score=2, away_score=1)
        breakdown = score_slot_for_user(player, group_slot)
        assert breakdown.matchup_type == "no_prediction"
        assert breakdown.total == Decimal("0")


# ---------- Cache writer tests ----------


@pytest.mark.django_db
class TestCacheWriters:
    def test_recompute_creates_row_with_engine_verdict(
        self, player, pre_round, group_slot, tur, bra,
    ):
        SlotPrediction.objects.create(
            user=player, prediction_round=pre_round, slot=group_slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        ActualResult.objects.create(slot=group_slot, home_score=2, away_score=1)
        # Wipe the row the post_save signal created so we can test the writer in isolation.
        SlotScore.objects.filter(user=player, slot=group_slot).delete()
        score = recompute_slot_for_user(player, group_slot)
        assert score.matchup_type == "exact"
        assert score.total == Decimal("6")
        assert score.earning_round_order == 0

    def test_recompute_stores_no_result_when_actual_missing(
        self, player, pre_round, group_slot, tur, bra,
    ):
        SlotPrediction.objects.create(
            user=player, prediction_round=pre_round, slot=group_slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        SlotScore.objects.filter(user=player, slot=group_slot).delete()
        score = recompute_slot_for_user(player, group_slot)
        assert score.matchup_type == SlotScore.NO_RESULT
        assert score.total == Decimal("0")

    def test_recompute_for_all_users_iterates_predictors(
        self, t, pre_round, group_slot, tur, bra,
    ):
        User = get_user_model()
        u1 = User.objects.create_user(email="a@x.com", username="a@x.com", nickname="A")
        u2 = User.objects.create_user(email="b@x.com", username="b@x.com", nickname="B")
        SlotPrediction.objects.create(
            user=u1, prediction_round=pre_round, slot=group_slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=u2, prediction_round=pre_round, slot=group_slot,
            home_team=tur, away_team=bra, home_score=3, away_score=0,
        )
        ActualResult.objects.create(slot=group_slot, home_score=2, away_score=1)
        # Wipe to test the bulk writer.
        SlotScore.objects.filter(slot=group_slot).delete()
        assert recompute_slot_for_all_users(group_slot) == 2
        rows = {s.user_id: s for s in SlotScore.objects.filter(slot=group_slot)}
        assert rows[u1.id].matchup_type == "exact"
        # 3-0 vs actual 2-1: same outcome (home win) but different goal diff → "result".
        assert rows[u2.id].matchup_type == "result"

    def test_recompute_user_all_slots_visits_each_slot(
        self, player, pre_round, group_slot, r16_slot, tur, bra,
    ):
        n = recompute_user_all_slots(player, group_slot.tournament)
        assert n == 2


# ---------- Signal tests ----------


@pytest.mark.django_db
class TestSignalInvalidation:
    def test_actual_result_save_populates_scores(
        self, player, pre_round, group_slot, tur, bra,
    ):
        SlotPrediction.objects.create(
            user=player, prediction_round=pre_round, slot=group_slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        # Until the actual result exists the row should be NO_RESULT.
        assert SlotScore.objects.get(user=player, slot=group_slot).matchup_type == SlotScore.NO_RESULT
        ActualResult.objects.create(slot=group_slot, home_score=2, away_score=1)
        # Signal should have updated the row.
        s = SlotScore.objects.get(user=player, slot=group_slot)
        assert s.matchup_type == "exact"
        assert s.total == Decimal("6")

    def test_actual_result_delete_reverts_scores(
        self, player, pre_round, group_slot, tur, bra,
    ):
        SlotPrediction.objects.create(
            user=player, prediction_round=pre_round, slot=group_slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        ar = ActualResult.objects.create(slot=group_slot, home_score=2, away_score=1)
        assert SlotScore.objects.get(user=player, slot=group_slot).total == Decimal("6")
        ar.delete()
        assert SlotScore.objects.get(user=player, slot=group_slot).matchup_type == SlotScore.NO_RESULT

    def test_prediction_save_updates_scores(
        self, player, pre_round, group_slot, tur, bra,
    ):
        ActualResult.objects.create(slot=group_slot, home_score=2, away_score=1)
        # Initial prediction: correct outcome, wrong diff → "result" (2 pts).
        sp = SlotPrediction.objects.create(
            user=player, prediction_round=pre_round, slot=group_slot,
            home_team=tur, away_team=bra, home_score=3, away_score=0,
        )
        assert SlotScore.objects.get(user=player, slot=group_slot).matchup_type == "result"
        # Edit to exact — signal should bump to "exact".
        sp.home_score = 2
        sp.away_score = 1
        sp.save()
        assert SlotScore.objects.get(user=player, slot=group_slot).matchup_type == "exact"

    def test_prediction_delete_clears_row_when_no_predictions_left(
        self, player, pre_round, group_slot, tur, bra,
    ):
        ActualResult.objects.create(slot=group_slot, home_score=2, away_score=1)
        sp = SlotPrediction.objects.create(
            user=player, prediction_round=pre_round, slot=group_slot,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        assert SlotScore.objects.filter(user=player, slot=group_slot).exists()
        sp.delete()
        assert not SlotScore.objects.filter(user=player, slot=group_slot).exists()
