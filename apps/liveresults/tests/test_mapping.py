"""Tests for matching a football-data payload to a BracketSlot."""

from apps.liveresults import mapping
from apps.liveresults.mapping import extract_team_codes, find_slot_for_match, normalize_tla


def _match(home_tla, away_tla, utc="2026-06-11T19:00:00Z", mid=1):
    return {
        "id": mid,
        "utcDate": utc,
        "homeTeam": {"tla": home_tla},
        "awayTeam": {"tla": away_tla},
    }


def test_extract_team_codes_uppercases():
    assert extract_team_codes(_match("tur", "bra")) == ("TUR", "BRA")


def test_normalize_tla_applies_override(monkeypatch):
    monkeypatch.setitem(mapping.TLA_OVERRIDES, "XXX", "TUR")
    assert normalize_tla("xxx") == "TUR"
    assert normalize_tla(None) == ""


def test_find_slot_matches_team_pair_either_order(tournament, slot):
    # Same order
    assert find_slot_for_match(tournament, _match("TUR", "BRA")) == slot
    # Reversed order (unordered pair)
    assert find_slot_for_match(tournament, _match("BRA", "TUR")) == slot


def test_find_slot_none_when_team_unknown(tournament, slot):
    assert find_slot_for_match(tournament, _match("TUR", "ARG")) is None


def test_find_slot_none_when_codes_missing(tournament, slot):
    assert find_slot_for_match(tournament, _match(None, None)) is None


def test_find_slot_ignores_unresolved_slots(tournament, group_stage):
    # A slot with no teams assigned must never match.
    from datetime import datetime, timezone as dt_tz

    from apps.tournament.models import BracketSlot
    BracketSlot.objects.create(
        tournament=tournament, stage=group_stage, position="R32-1",
        scheduled_kickoff=datetime(2026, 6, 28, 19, 0, tzinfo=dt_tz.utc),
    )
    assert find_slot_for_match(tournament, _match("TUR", "BRA")) is None
