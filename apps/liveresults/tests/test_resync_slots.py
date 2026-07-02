"""Tests for the resync_slots force re-pull command.

Pins the repair path for stuck-finalized rows the normal sync never revisits:
a fake client feeds the authoritative FINISHED payload and the command must
rewrite ActualResult through the sync write path, bypassing finalized. See
apps/liveresults/management/commands/resync_slots.py.
"""

from datetime import datetime, timezone as dt_tz

import pytest
from django.core.management import call_command

from apps.liveresults.management.commands import resync_slots as cmd_mod
from apps.liveresults.models import MatchSync
from apps.tournament.models import ActualResult, BracketSlot, Stage


@pytest.fixture
def ko_stage(tournament):
    return Stage.objects.create(
        tournament=tournament, kind=Stage.R32, order=1,
        points_exact=6, points_diff=4, points_result=2,
    )


@pytest.fixture
def finalized_slot(tournament, ko_stage, teams):
    """A finalized penalty match whose stored row is the hybrid-stale shape:
    penalties corrected by hand (2-3) but aet still the mid-shootout capture
    (4-4) — the shape fix_penalty_aet can't repair."""
    slot = BracketSlot.objects.create(
        tournament=tournament, stage=ko_stage, position="R32-3",
        scheduled_kickoff=datetime(2026, 6, 29, 22, 0, tzinfo=dt_tz.utc),
        home_team_actual=teams["TUR"], away_team_actual=teams["BRA"],
    )
    MatchSync.objects.create(slot=slot, external_id="537418", status="FINISHED", finalized=True)
    ActualResult.objects.create(
        slot=slot, home_score=1, away_score=1,
        went_to_extra_time=True, went_to_penalties=True,
        home_score_aet=4, away_score_aet=4,
        home_penalties=2, away_penalties=3,
        penalty_winner=teams["BRA"], source="API",
    )
    return slot


PENALTY_PAYLOAD = {
    "id": "537418",
    "status": "FINISHED",
    "score": {
        "winner": "AWAY_TEAM",
        "duration": "PENALTY_SHOOTOUT",
        "fullTime": {"home": 3, "away": 4},
        "regularTime": {"home": 1, "away": 1},
        "extraTime": {"home": 0, "away": 0},
        "penalties": {"home": 2, "away": 3},
    },
}


def _patch(monkeypatch, matches):
    class _C:
        is_configured = True

        def get_competition_matches(self, **kwargs):
            return matches

    monkeypatch.setattr(cmd_mod, "FootballDataClient", lambda *a, **k: _C())


@pytest.mark.django_db
def test_rewrites_stale_finalized_row_from_api(finalized_slot, teams, monkeypatch):
    _patch(monkeypatch, [PENALTY_PAYLOAD])

    call_command("resync_slots", "R32-3")

    r = ActualResult.objects.get(slot=finalized_slot)
    assert (r.home_score, r.away_score) == (1, 1)
    assert (r.home_score_aet, r.away_score_aet) == (1, 1)  # 4-4 stale repaired
    assert (r.home_penalties, r.away_penalties) == (2, 3)
    assert r.penalty_winner == teams["BRA"]
    ms = MatchSync.objects.get(slot=finalized_slot)
    assert ms.finalized is True


@pytest.mark.django_db
def test_unchanged_row_not_rewritten(finalized_slot, monkeypatch, capsys):
    _patch(monkeypatch, [PENALTY_PAYLOAD])

    call_command("resync_slots", "R32-3")  # repairs
    call_command("resync_slots", "R32-3")  # second run must be a no-op

    assert "[unchanged]" in capsys.readouterr().out


@pytest.mark.django_db
def test_dry_run_writes_nothing(finalized_slot, monkeypatch, capsys):
    _patch(monkeypatch, [PENALTY_PAYLOAD])

    call_command("resync_slots", "R32-3", "--dry-run")

    r = ActualResult.objects.get(slot=finalized_slot)
    assert (r.home_score_aet, r.away_score_aet) == (4, 4)  # untouched
    assert "would write" in capsys.readouterr().out


@pytest.mark.django_db
def test_unmapped_slot_errors_cleanly(tournament, ko_stage, teams, monkeypatch, capsys):
    BracketSlot.objects.create(
        tournament=tournament, stage=ko_stage, position="R32-7",
        scheduled_kickoff=datetime(2026, 7, 1, 19, 0, tzinfo=dt_tz.utc),
        home_team_actual=teams["TUR"], away_team_actual=teams["BRA"],
    )
    _patch(monkeypatch, [PENALTY_PAYLOAD])

    call_command("resync_slots", "R32-7")

    assert "map_external_ids" in capsys.readouterr().err


@pytest.mark.django_db
def test_unknown_position_errors_cleanly(tournament, monkeypatch, capsys):
    _patch(monkeypatch, [])

    call_command("resync_slots", "R99-9")

    assert "no such slot" in capsys.readouterr().err


@pytest.mark.django_db
def test_payload_missing_from_api_errors_cleanly(finalized_slot, monkeypatch, capsys):
    _patch(monkeypatch, [])  # API returns nothing for this match

    call_command("resync_slots", "R32-3")

    r = ActualResult.objects.get(slot=finalized_slot)
    assert (r.home_score_aet, r.away_score_aet) == (4, 4)  # untouched
    assert "not in the API response" in capsys.readouterr().err


@pytest.mark.django_db
def test_no_usable_score_skips(finalized_slot, monkeypatch, capsys):
    _patch(monkeypatch, [{"id": "537418", "status": "TIMED", "score": {}}])

    call_command("resync_slots", "R32-3")

    r = ActualResult.objects.get(slot=finalized_slot)
    assert (r.home_score_aet, r.away_score_aet) == (4, 4)  # untouched
    assert "no usable score" in capsys.readouterr().out
