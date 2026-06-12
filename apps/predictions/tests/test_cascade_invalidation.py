"""Downstream invalidation when upstream predictions change matchups.

Product rule: if editing a prediction changes the derived matchup of a
downstream slot, the old prediction on that slot must be deleted — the slot
must look never-predicted. Covers:
- R32 winner change → dependent R16 prediction deleted (and recursively QF…)
- winner unchanged (score-only edit) → downstream predictions survive
- group standings flip → group-derived R32 prediction deleted
- other (closed) rounds are never touched
- form layer: a stale stored team can't survive a re-save (derived team wins)
- save view: invalidation runs and stale rows come back as hx-swap-oob swaps
"""

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.predictions.cascade import downstream_slots, invalidate_stale_predictions
from apps.predictions.forms import SlotPredictionForm
from apps.predictions.models import SlotPrediction
from apps.tournament.models import BracketSlot, PredictionRound, Stage


@pytest.fixture
def stage_r32(tournament):
    return Stage.objects.create(
        tournament=tournament, kind=Stage.R32, order=1,
        points_exact=8, points_diff=5, points_result=2,
        penalty_loser_pct=Decimal("0.60"),
    )


@pytest.fixture
def stage_qf(tournament):
    return Stage.objects.create(
        tournament=tournament, kind=Stage.QF, order=3,
        points_exact=10, points_diff=7, points_result=4,
        penalty_loser_pct=Decimal("0.60"),
    )


@pytest.fixture
def full_round(prediction_round, stage_group, stage_r32, stage_r16, stage_qf):
    prediction_round.editable_stages.set([stage_group, stage_r32, stage_r16, stage_qf])
    return prediction_round


@pytest.fixture
def r32_a(tournament, stage_r32, team_tur, team_bra):
    """R32 slot with known (actual) teams: TUR vs BRA."""
    return BracketSlot.objects.create(
        tournament=tournament, stage=stage_r32, position="R32-1",
        scheduled_kickoff=timezone.now() + timedelta(days=12),
        home_team_actual=team_tur, away_team_actual=team_bra,
    )


@pytest.fixture
def r32_b(tournament, stage_r32, team_arg, team_ger):
    """R32 slot with known (actual) teams: ARG vs GER."""
    return BracketSlot.objects.create(
        tournament=tournament, stage=stage_r32, position="R32-2",
        scheduled_kickoff=timezone.now() + timedelta(days=12),
        home_team_actual=team_arg, away_team_actual=team_ger,
    )


@pytest.fixture
def r16_cascaded(tournament, stage_r16, r32_a, r32_b):
    """R16 slot fed by the winners of R32-1 (home) and R32-2 (away)."""
    return BracketSlot.objects.create(
        tournament=tournament, stage=stage_r16, position="R16-1",
        scheduled_kickoff=timezone.now() + timedelta(days=16),
        home_source_slot=r32_a, home_source_kind=BracketSlot.SOURCE_KIND_WINNER,
        away_source_slot=r32_b, away_source_kind=BracketSlot.SOURCE_KIND_WINNER,
    )


@pytest.fixture
def qf_cascaded(tournament, stage_qf, r16_cascaded):
    """QF slot whose home side is the winner of R16-1; away side is free."""
    return BracketSlot.objects.create(
        tournament=tournament, stage=stage_qf, position="QF-1",
        scheduled_kickoff=timezone.now() + timedelta(days=20),
        home_source_slot=r16_cascaded, home_source_kind=BracketSlot.SOURCE_KIND_WINNER,
    )


def _predict(user, pr, slot, home_team, away_team, home_score, away_score):
    return SlotPrediction.objects.create(
        user=user, prediction_round=pr, slot=slot,
        home_team=home_team, away_team=away_team,
        home_score=home_score, away_score=away_score,
    )


@pytest.fixture
def bracket_preds(user, full_round, r32_a, r32_b, r16_cascaded,
                  team_tur, team_bra, team_arg, team_ger):
    """Consistent bracket: TUR and ARG win their R32 ties, meet in R16."""
    return {
        "r32_a": _predict(user, full_round, r32_a, team_tur, team_bra, 2, 1),
        "r32_b": _predict(user, full_round, r32_b, team_arg, team_ger, 1, 0),
        "r16": _predict(user, full_round, r16_cascaded, team_tur, team_arg, 2, 0),
    }


@pytest.mark.django_db
class TestInvalidateStalePredictions:
    def test_upstream_winner_change_deletes_dependent_prediction(
        self, user, full_round, r16_cascaded, bracket_preds, team_bra,
    ):
        # User flips R32-1 to a BRA win — the stored R16 (TUR vs ARG) is stale.
        pred = bracket_preds["r32_a"]
        pred.home_score, pred.away_score = 0, 1
        pred.save()

        deleted = invalidate_stale_predictions(user, full_round)

        assert [s.id for s in deleted] == [r16_cascaded.id]
        assert not SlotPrediction.objects.filter(user=user, slot=r16_cascaded).exists()

    def test_score_change_with_same_winner_keeps_dependents(
        self, user, full_round, r16_cascaded, bracket_preds,
    ):
        pred = bracket_preds["r32_a"]
        pred.home_score, pred.away_score = 5, 0  # still a TUR win
        pred.save()

        deleted = invalidate_stale_predictions(user, full_round)

        assert deleted == []
        assert SlotPrediction.objects.filter(user=user, slot=r16_cascaded).exists()

    def test_invalidation_cascades_through_later_stages(
        self, user, full_round, r16_cascaded, qf_cascaded, bracket_preds,
        team_tur, team_arg,
    ):
        # QF home = winner of R16-1 (TUR per current bracket); away is a free pick.
        _predict(user, full_round, qf_cascaded, team_tur, team_arg, 1, 0)

        pred = bracket_preds["r32_a"]
        pred.home_score, pred.away_score = 0, 3  # BRA win → R16 stale → QF stale
        pred.save()

        deleted = invalidate_stale_predictions(user, full_round)

        assert {s.id for s in deleted} == {r16_cascaded.id, qf_cascaded.id}
        assert not SlotPrediction.objects.filter(
            user=user, slot__in=[r16_cascaded, qf_cascaded],
        ).exists()

    def test_free_pick_side_never_triggers_deletion(
        self, user, full_round, qf_cascaded, bracket_preds, team_tur, team_ger,
    ):
        # QF away side has no source — changing nothing upstream of it must
        # not delete the prediction as long as the sourced (home) side holds.
        _predict(user, full_round, qf_cascaded, team_tur, team_ger, 2, 1)

        deleted = invalidate_stale_predictions(user, full_round)

        assert deleted == []

    def test_group_standings_flip_deletes_group_derived_prediction(
        self, user, full_round, group_slot, tournament, stage_r32,
        team_tur, team_bra, team_ger,
    ):
        r32_from_group = BracketSlot.objects.create(
            tournament=tournament, stage=stage_r32, position="R32-3",
            scheduled_kickoff=timezone.now() + timedelta(days=12),
            home_source_group_letter="A", home_source_group_position=1,
        )
        group_pred = _predict(user, full_round, group_slot, team_tur, team_bra, 2, 0)
        _predict(user, full_round, r32_from_group, team_tur, team_ger, 1, 0)

        # Flip the group match → BRA now tops Group A → R32-3 matchup changed.
        group_pred.home_score, group_pred.away_score = 0, 2
        group_pred.save()

        deleted = invalidate_stale_predictions(user, full_round)

        assert [s.id for s in deleted] == [r32_from_group.id]
        assert not SlotPrediction.objects.filter(user=user, slot=r32_from_group).exists()

    def test_other_rounds_are_never_touched(
        self, user, full_round, tournament, r32_a, r16_cascaded, bracket_preds,
        team_tur, team_arg, stage_group, stage_r32, stage_r16, stage_qf,
    ):
        closed_round = PredictionRound.objects.create(
            tournament=tournament, name="Closed", order=99,
            deadline=timezone.now() - timedelta(days=1), weight=Decimal("0.50"),
        )
        closed_round.editable_stages.set([stage_group, stage_r32, stage_r16, stage_qf])
        closed_pred = _predict(user, closed_round, r16_cascaded, team_tur, team_arg, 3, 1)

        pred = bracket_preds["r32_a"]
        pred.home_score, pred.away_score = 0, 1
        pred.save()

        invalidate_stale_predictions(user, full_round)

        assert SlotPrediction.objects.filter(pk=closed_pred.pk).exists()


@pytest.mark.django_db
class TestDownstreamSlots:
    def test_transitive_closure_in_stage_order(self, r32_a, r16_cascaded, qf_cascaded):
        result = downstream_slots(r32_a)
        assert [s.id for s in result] == [r16_cascaded.id, qf_cascaded.id]

    def test_leaf_slot_has_no_downstream(self, qf_cascaded):
        assert downstream_slots(qf_cascaded) == []


@pytest.mark.django_db
class TestFormDerivedTeamOverridesStaleInstance:
    def test_resave_replaces_stale_teams_with_derived_ones(
        self, user, full_round, r16_cascaded, bracket_preds, team_bra, team_arg,
    ):
        # R32-1 flipped to BRA, but the stored R16 row still says TUR.
        pred = bracket_preds["r32_a"]
        pred.home_score, pred.away_score = 0, 1
        pred.save()
        stale_r16 = bracket_preds["r16"]

        form = SlotPredictionForm(
            data={"home_score": 1, "away_score": 0},
            instance=stale_r16,
            user=user, prediction_round=full_round, slot=r16_cascaded,
        )
        assert form.is_valid(), form.errors
        saved = form.save()

        assert saved.home_team_id == team_bra.id
        assert saved.away_team_id == team_arg.id

    def test_form_initial_shows_derived_team_not_stored_one(
        self, user, full_round, r16_cascaded, bracket_preds, team_bra,
    ):
        pred = bracket_preds["r32_a"]
        pred.home_score, pred.away_score = 0, 1
        pred.save()

        form = SlotPredictionForm(
            instance=bracket_preds["r16"],
            user=user, prediction_round=full_round, slot=r16_cascaded,
        )
        # BoundField.initial drives both the hidden input and the disabled
        # field's submit value — it must be the derived team.
        assert form["home_team"].initial == team_bra


@pytest.mark.django_db
class TestSaveViewInvalidation:
    def test_htmx_save_deletes_stale_row_and_sends_oob_swap(
        self, client, user, full_round, r32_a, r16_cascaded, bracket_preds,
        team_tur, team_bra,
    ):
        client.force_login(user)
        url = reverse("slot_prediction_save", args=[full_round.id, r32_a.id])

        r = client.post(
            url,
            {"home_team": team_tur.id, "away_team": team_bra.id,
             "home_score": 0, "away_score": 1},
            HTTP_HX_REQUEST="true",
        )

        assert r.status_code == 200
        assert not SlotPrediction.objects.filter(user=user, slot=r16_cascaded).exists()
        content = r.content.decode()
        assert f'id="slot-row-{r16_cascaded.id}"' in content
        assert 'hx-swap-oob="outerHTML"' in content

    def test_draw_to_decisive_edit_with_stale_penalty_payload_invalidates(
        self, client, user, full_round, r32_a, r16_cascaded, bracket_preds,
        team_tur, team_bra,
    ):
        """Reported in the wild: R32 draw (with pens) edited to a decisive
        score did NOT update downstream matchups. Cause: the browser still
        submits the CSS-hidden penalty inputs, validation rejected the save
        silently, so invalidation never ran."""
        pred = bracket_preds["r32_a"]
        pred.home_score, pred.away_score = 1, 1
        pred.penalty_winner, pred.home_penalties, pred.away_penalties = team_tur, 4, 2
        pred.save()

        client.force_login(user)
        r = client.post(
            reverse("slot_prediction_save", args=[full_round.id, r32_a.id]),
            {"home_team": team_tur.id, "away_team": team_bra.id,
             "home_score": 0, "away_score": 1,
             # stale shootout values from the now-hidden penalty section
             "home_penalties": 4, "away_penalties": 2},
            HTTP_HX_REQUEST="true",
        )

        assert r.status_code == 200
        pred.refresh_from_db()
        assert (pred.home_score, pred.away_score) == (0, 1)
        assert pred.penalty_winner_id is None
        # BRA now wins R32-1 → the stored R16 (TUR vs ARG) must be gone.
        assert not SlotPrediction.objects.filter(user=user, slot=r16_cascaded).exists()
        assert f'id="slot-row-{r16_cascaded.id}"' in r.content.decode()

    def test_htmx_save_with_same_winner_sends_refreshed_dependent_rows_only(
        self, client, user, full_round, r32_a, r16_cascaded, bracket_preds,
        team_tur, team_bra,
    ):
        client.force_login(user)
        url = reverse("slot_prediction_save", args=[full_round.id, r32_a.id])

        r = client.post(
            url,
            {"home_team": team_tur.id, "away_team": team_bra.id,
             "home_score": 4, "away_score": 0},  # TUR still wins
            HTTP_HX_REQUEST="true",
        )

        assert r.status_code == 200
        # Dependent prediction untouched, but its row is still refreshed OOB
        # (displayed teams could have changed in other scenarios).
        assert SlotPrediction.objects.filter(user=user, slot=r16_cascaded).exists()
        assert f'id="slot-row-{r16_cascaded.id}"' in r.content.decode()


@pytest.mark.django_db
class TestCarryOverPrefillSkippedWhenMatchupChanged:
    def _make_prev_round(self, tournament, stages, order=0, days_ago=1):
        prev = PredictionRound.objects.create(
            tournament=tournament, name=f"Prev-{order}", order=order,
            deadline=timezone.now() - timedelta(days=days_ago), weight=Decimal("1.00"),
        )
        prev.editable_stages.set(stages)
        return prev

    def test_prefill_dropped_when_derived_matchup_differs(
        self, client, user, tournament, full_round,
        r32_a, r32_b, r16_cascaded,
        stage_group, stage_r32, stage_r16, stage_qf,
        team_tur, team_bra, team_arg, team_ger,
    ):
        # full_round becomes order 1; a closed earlier round holds the old bracket.
        full_round.order = 1
        full_round.save()
        prev = self._make_prev_round(
            tournament, [stage_group, stage_r32, stage_r16, stage_qf])
        _predict(user, prev, r32_a, team_tur, team_bra, 2, 1)
        _predict(user, prev, r32_b, team_arg, team_ger, 1, 0)
        _predict(user, prev, r16_cascaded, team_tur, team_arg, 19, 3)

        # In the open round the user now has BRA winning R32-1.
        _predict(user, full_round, r32_a, team_tur, team_bra, 0, 1)

        client.force_login(user)
        r = client.get(reverse(
            "predict_knockout_stage_step", args=[full_round.id, "R16"]))

        assert r.status_code == 200
        content = r.content.decode()
        # Matchup changed (BRA vs ARG now) → the 19-3 scoreline must not carry
        # over; the score input renders empty.
        assert re.search(r'name="home_score"\s+value=""', content)
        assert not re.search(r'name="home_score"\s+value="19"', content)
        assert team_bra.name_tr in content

    def test_prefill_kept_when_matchup_unchanged(
        self, client, user, tournament, full_round,
        r32_a, r32_b, r16_cascaded,
        stage_group, stage_r32, stage_r16, stage_qf,
        team_tur, team_bra, team_arg, team_ger,
    ):
        full_round.order = 1
        full_round.save()
        prev = self._make_prev_round(
            tournament, [stage_group, stage_r32, stage_r16, stage_qf])
        _predict(user, prev, r32_a, team_tur, team_bra, 2, 1)
        _predict(user, prev, r32_b, team_arg, team_ger, 1, 0)
        _predict(user, prev, r16_cascaded, team_tur, team_arg, 19, 3)

        # Same winners re-predicted in the open round → matchup unchanged.
        _predict(user, full_round, r32_a, team_tur, team_bra, 3, 2)

        client.force_login(user)
        r = client.get(reverse(
            "predict_knockout_stage_step", args=[full_round.id, "R16"]))

        assert r.status_code == 200
        assert re.search(r'name="home_score"\s+value="19"', r.content.decode())
