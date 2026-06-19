"""Tests for the actual-bracket resolver.

Covers knockout winner/loser propagation (regular, extra time, penalties) and
R32 filling from completed group standings. ActualResult.save fires the resolver
via signal, but these assert final slot state so they're robust either way.
"""

from datetime import datetime, timedelta, timezone as dt_tz

import pytest

from apps.tournament.models import ActualResult, BracketSlot, Stage, Team, Tournament
from apps.tournament.resolver import resolve_bracket

KICK = datetime(2026, 6, 28, 19, 0, tzinfo=dt_tz.utc)


@pytest.fixture
def tournament(db):
    return Tournament.objects.create(
        name="T", slug="t", start_date="2026-06-11", end_date="2026-07-19", is_active=True,
    )


def _stage(tournament, kind, order):
    return Stage.objects.create(
        tournament=tournament, kind=kind, order=order,
        points_exact=6, points_diff=4, points_result=2,
    )


def _team(tournament, code):
    return Team.objects.create(tournament=tournament, code=code, name_tr=code)


def _slot(tournament, stage, position, home=None, away=None, kickoff=KICK, **kw):
    return BracketSlot.objects.create(
        tournament=tournament, stage=stage, position=position,
        scheduled_kickoff=kickoff, home_team_actual=home, away_team_actual=away, **kw,
    )


# ---------- knockout feed propagation ----------


@pytest.fixture
def ko_pair(tournament):
    """Two R32 slots feeding one R16 slot (both via WINNER)."""
    r32 = _stage(tournament, Stage.R32, 1)
    r16 = _stage(tournament, Stage.R16, 2)
    tur, bra, esp, ger = (_team(tournament, c) for c in ("TUR", "BRA", "ESP", "GER"))
    s1 = _slot(tournament, r32, "R32-1", tur, bra)
    s2 = _slot(tournament, r32, "R32-2", esp, ger)
    dest = _slot(tournament, r16, "R16-1", kickoff=KICK + timedelta(days=4),
                 home_source_slot=s1, home_source_kind=BracketSlot.SOURCE_KIND_WINNER,
                 away_source_slot=s2, away_source_kind=BracketSlot.SOURCE_KIND_WINNER)
    return {"s1": s1, "s2": s2, "dest": dest,
            "tur": tur, "bra": bra, "esp": esp, "ger": ger}


def test_regular_winner_propagates(ko_pair, tournament):
    ActualResult.objects.create(slot=ko_pair["s1"], home_score=2, away_score=0)  # TUR
    ActualResult.objects.create(slot=ko_pair["s2"], home_score=0, away_score=1)  # GER
    resolve_bracket(tournament)
    dest = BracketSlot.objects.get(pk=ko_pair["dest"].pk)
    assert dest.home_team_actual == ko_pair["tur"]
    assert dest.away_team_actual == ko_pair["ger"]


def test_extra_time_winner_uses_aet(ko_pair, tournament):
    # 90' draw, won 2-1 in extra time (no penalties) → home advances.
    ActualResult.objects.create(
        slot=ko_pair["s1"], home_score=1, away_score=1,
        went_to_extra_time=True, home_score_aet=2, away_score_aet=1,
    )
    resolve_bracket(tournament)
    dest = BracketSlot.objects.get(pk=ko_pair["dest"].pk)
    assert dest.home_team_actual == ko_pair["tur"]


def test_penalty_winner_propagates(ko_pair, tournament):
    ActualResult.objects.create(
        slot=ko_pair["s1"], home_score=1, away_score=1,
        went_to_extra_time=True, went_to_penalties=True,
        home_penalties=2, away_penalties=4, penalty_winner=ko_pair["bra"],
        home_score_aet=1, away_score_aet=1,
    )
    resolve_bracket(tournament)
    dest = BracketSlot.objects.get(pk=ko_pair["dest"].pk)
    assert dest.home_team_actual == ko_pair["bra"]  # shootout winner advances


def test_loser_propagates_to_third_place(tournament):
    sf = _stage(tournament, Stage.SF, 4)
    third = _stage(tournament, Stage.THIRD, 5)
    arg, fra = _team(tournament, "ARG"), _team(tournament, "FRA")
    sf1 = _slot(tournament, sf, "SF-1", arg, fra)
    third_slot = _slot(tournament, third, "Third", kickoff=KICK + timedelta(days=10),
                       home_source_slot=sf1, home_source_kind=BracketSlot.SOURCE_KIND_LOSER)
    ActualResult.objects.create(slot=sf1, home_score=3, away_score=1)  # ARG win → FRA loses
    resolve_bracket(tournament)
    assert BracketSlot.objects.get(pk=third_slot.pk).home_team_actual == fra


def test_no_result_leaves_destination_unresolved(ko_pair, tournament):
    resolve_bracket(tournament)
    dest = BracketSlot.objects.get(pk=ko_pair["dest"].pk)
    assert dest.home_team_actual_id is None and dest.away_team_actual_id is None


# ---------- R32 from group standings ----------


def test_r32_filled_from_group_first_place(tournament):
    group = _stage(tournament, Stage.GROUP, 0)
    r32 = _stage(tournament, Stage.R32, 1)
    a, b, c, d = (_team(tournament, code) for code in ("AAA", "BBB", "CCC", "DDD"))
    fixtures = [
        ("GroupA-M1", a, b, 2, 0),  # AAA
        ("GroupA-M2", c, d, 1, 1),
        ("GroupA-M3", a, c, 1, 0),  # AAA
        ("GroupA-M4", b, d, 0, 0),
        ("GroupA-M5", a, d, 3, 0),  # AAA → 9 pts, 1st
        ("GroupA-M6", b, c, 1, 1),
    ]
    for pos, h, aw, hs, as_ in fixtures:
        slot = _slot(tournament, group, pos, h, aw, kickoff=KICK - timedelta(days=10))
        ActualResult.objects.create(slot=slot, home_score=hs, away_score=as_)

    dest = _slot(tournament, r32, "R32-1",
                 home_source_group_letter="A", home_source_group_position=1)
    resolve_bracket(tournament)
    assert BracketSlot.objects.get(pk=dest.pk).home_team_actual == a


def test_r32_not_filled_until_group_complete(tournament):
    group = _stage(tournament, Stage.GROUP, 0)
    r32 = _stage(tournament, Stage.R32, 1)
    a, b = _team(tournament, "AAA"), _team(tournament, "BBB")
    s = _slot(tournament, group, "GroupA-M1", a, b, kickoff=KICK - timedelta(days=10))
    ActualResult.objects.create(slot=s, home_score=1, away_score=0)
    # Group A has 6 slots seeded normally; here only 1 exists with a result, but
    # the gate is "all existing group slots resolved". Add an unplayed one:
    _slot(tournament, group, "GroupA-M2", a, b, kickoff=KICK - timedelta(days=9))
    dest = _slot(tournament, r32, "R32-1",
                 home_source_group_letter="A", home_source_group_position=1)
    resolve_bracket(tournament)
    assert BracketSlot.objects.get(pk=dest.pk).home_team_actual_id is None
