"""Tests for sync_live_matches — the core live-write path.

A fake client feeds canned payloads, so these pin the write/skip/finalize
behaviour without hitting the network.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.liveresults import sync
from apps.liveresults.models import MatchSync
from apps.tournament.models import ActualResult, BracketSlot


def _fake_client(matches):
    class _C:
        is_configured = True

        def get_competition_matches(self, **kwargs):
            return matches

    return _C()


def _match(ext, status, score, minute=None, injury=None):
    return {"id": ext, "status": status, "minute": minute, "injuryTime": injury, "score": score}


def _regular(home, away):
    return {"duration": "REGULAR", "fullTime": {"home": home, "away": away}}


@pytest.fixture
def live_slot(tournament, group_stage, teams):
    """A group slot kicked off 30min ago (inside the live window), mapped to ext '999'."""
    s = BracketSlot.objects.create(
        tournament=tournament, stage=group_stage, position="GroupA-M1",
        scheduled_kickoff=timezone.now() - timedelta(minutes=30),
        home_team_actual=teams["TUR"], away_team_actual=teams["BRA"],
    )
    MatchSync.objects.create(slot=s, external_id="999", status="TIMED")
    return s


def _patch(monkeypatch, matches):
    monkeypatch.setattr(sync, "FootballDataClient", lambda *a, **k: _fake_client(matches))


def test_no_live_window_makes_no_api_call(tournament, group_stage, teams, monkeypatch):
    # Slot far in the past → outside the window → API must not be called.
    BracketSlot.objects.create(
        tournament=tournament, stage=group_stage, position="GroupA-M9",
        scheduled_kickoff=timezone.now() - timedelta(days=3),
        home_team_actual=teams["TUR"], away_team_actual=teams["BRA"],
    )
    calls = {"n": 0}

    class _C:
        is_configured = True

        def get_competition_matches(self, **kwargs):
            calls["n"] += 1
            return []

    monkeypatch.setattr(sync, "FootballDataClient", lambda *a, **k: _C())
    report = sync.sync_live_matches(tournament)
    assert calls["n"] == 0
    assert report.fetched == 0


def test_match_past_live_cap_drops_out_of_window(tournament, group_stage, teams):
    # Group cap is 140 min; a slot 2.5h past kickoff is no longer "live".
    s = BracketSlot.objects.create(
        tournament=tournament, stage=group_stage, position="GroupA-M5",
        scheduled_kickoff=timezone.now() - timedelta(hours=2, minutes=30),
        home_team_actual=teams["TUR"], away_team_actual=teams["BRA"],
    )
    MatchSync.objects.create(slot=s, external_id="555", status="IN_PLAY")
    assert sync.slots_in_live_window(tournament) == []


def test_writes_live_group_result(live_slot, monkeypatch):
    _patch(monkeypatch, [_match("999", "IN_PLAY", _regular(1, 0), minute=90, injury=3)])
    report = sync.sync_live_matches(live_slot.tournament)

    assert report.written == 1
    ar = ActualResult.objects.get(slot=live_slot)
    assert (ar.home_score, ar.away_score) == (1, 0)
    assert ar.source == ActualResult.SOURCE_API
    ms = MatchSync.objects.get(slot=live_slot)
    assert ms.status == "IN_PLAY" and ms.finalized is False
    assert ms.minute == 90 and ms.injury_time == 3   # shown as 90+3′


def test_finished_sets_finalized_and_excludes_next_pass(live_slot, monkeypatch):
    _patch(monkeypatch, [_match("999", "FINISHED", _regular(2, 1))])
    sync.sync_live_matches(live_slot.tournament)

    ms = MatchSync.objects.get(slot=live_slot)
    assert ms.finalized is True
    # A finalized slot drops out of the live window → no further work.
    assert sync.slots_in_live_window(live_slot.tournament) == []


def test_unchanged_result_not_rewritten(live_slot, monkeypatch):
    _patch(monkeypatch, [_match("999", "IN_PLAY", _regular(1, 1))])
    sync.sync_live_matches(live_slot.tournament)
    report2 = sync.sync_live_matches(live_slot.tournament)
    assert report2.written == 0 and report2.unchanged == 1


def test_manual_result_never_overwritten(live_slot, monkeypatch):
    """A wizard-entered (MANUAL) row is authoritative: the poller keeps
    tracking status but must not touch the result."""
    ActualResult.objects.create(
        slot=live_slot, home_score=2, away_score=1,
        source=ActualResult.SOURCE_MANUAL,
    )
    _patch(monkeypatch, [_match("999", "IN_PLAY", _regular(0, 0), minute=55)])
    report = sync.sync_live_matches(live_slot.tournament)

    assert report.manual_kept == 1 and report.written == 0
    ar = ActualResult.objects.get(slot=live_slot)
    assert (ar.home_score, ar.away_score) == (2, 1)
    assert ar.source == ActualResult.SOURCE_MANUAL
    # Status/minute still tracked — the live badge keeps working.
    ms = MatchSync.objects.get(slot=live_slot)
    assert ms.status == "IN_PLAY" and ms.minute == 55


def test_manual_result_still_finalizes_on_finished(live_slot, monkeypatch):
    """FINISHED still flips `finalized` on a manual-locked slot so it drops out
    of the live window — polling stops, the manual result stands."""
    ActualResult.objects.create(
        slot=live_slot, home_score=2, away_score=1,
        source=ActualResult.SOURCE_MANUAL,
    )
    _patch(monkeypatch, [_match("999", "FINISHED", _regular(0, 0))])
    report = sync.sync_live_matches(live_slot.tournament)

    assert report.manual_kept == 1 and report.finalized == 1
    ar = ActualResult.objects.get(slot=live_slot)
    assert (ar.home_score, ar.away_score) == (2, 1)
    ms = MatchSync.objects.get(slot=live_slot)
    assert ms.finalized is True
    assert sync.slots_in_live_window(live_slot.tournament) == []


def test_knockout_slot_without_teams_awaits_resolution(tournament, group_stage, monkeypatch):
    s = BracketSlot.objects.create(
        tournament=tournament, stage=group_stage, position="R32-1",
        scheduled_kickoff=timezone.now() - timedelta(minutes=10),
    )
    MatchSync.objects.create(slot=s, external_id="888", status="TIMED")
    _patch(monkeypatch, [_match("888", "IN_PLAY", _regular(1, 0))])

    report = sync.sync_live_matches(tournament)
    assert report.awaiting_teams == 1
    assert not ActualResult.objects.filter(slot=s).exists()


def test_penalty_resolves_winner_team_from_score(live_slot, monkeypatch):
    # fullTime folds the shootout goals into the 120' draw (1-1 + 5-4 → 6-5).
    score = {"duration": "PENALTY_SHOOTOUT",
             "fullTime": {"home": 6, "away": 5},
             "regularTime": {"home": 1, "away": 1},
             "penalties": {"home": 5, "away": 4}}
    _patch(monkeypatch, [_match("999", "FINISHED", score)])
    sync.sync_live_matches(live_slot.tournament)

    ar = ActualResult.objects.get(slot=live_slot)
    assert ar.went_to_penalties is True
    assert ar.penalty_winner == live_slot.home_team_actual  # 5 > 4
    # The stored 120' score is the clean draw, not the penalty-inflated 6-5.
    assert (ar.home_score, ar.away_score) == (1, 1)
    assert (ar.home_score_aet, ar.away_score_aet) == (1, 1)
    assert (ar.effective_home_score, ar.effective_away_score) == (1, 1)


def test_dry_run_writes_nothing(live_slot, monkeypatch):
    _patch(monkeypatch, [_match("999", "IN_PLAY", _regular(1, 0))])
    report = sync.sync_live_matches(live_slot.tournament, dry_run=True)

    assert report.written == 1  # counted as would-write
    assert not ActualResult.objects.filter(slot=live_slot).exists()
    ms = MatchSync.objects.get(slot=live_slot)
    assert ms.status == "TIMED"  # untouched in dry-run
