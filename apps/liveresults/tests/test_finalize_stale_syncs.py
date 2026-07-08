"""Tests for the finalize_stale_syncs management command."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.liveresults.models import MatchSync
from apps.tournament.models import ActualResult, BracketSlot


def _slot(tournament, stage, teams, position, kickoff):
    return BracketSlot.objects.create(
        tournament=tournament, stage=stage, position=position,
        scheduled_kickoff=kickoff,
        home_team_actual=teams["TUR"], away_team_actual=teams["BRA"],
    )


@pytest.mark.django_db
def test_finalizes_in_play_match_past_cap_with_result(tournament, group_stage, teams):
    """The core gap: stuck IN_PLAY, past cap, has a scored result → finalized."""
    now = timezone.now()
    s = _slot(tournament, group_stage, teams, "GroupA-M1", now - timedelta(hours=3))
    ms = MatchSync.objects.create(slot=s, external_id="1", status="IN_PLAY")
    ActualResult.objects.create(slot=s, home_score=0, away_score=1, source="API")

    call_command("finalize_stale_syncs")

    ms.refresh_from_db()
    assert ms.finalized is True
    assert ms.status == "FINISHED"


@pytest.mark.django_db
def test_in_play_knockout_within_hard_cap_left_polling(tournament, knockout_stage, teams):
    """A knockout past its stage cap but still IN_PLAY within the hard cap: the
    poller can still grab the real FINISHED score, so the row must not be frozen."""
    now = timezone.now()
    s = _slot(tournament, knockout_stage, teams, "R16-4", now - timedelta(hours=4))
    ms = MatchSync.objects.create(slot=s, external_id="6", status="IN_PLAY")
    ActualResult.objects.create(slot=s, home_score=0, away_score=1, source="API")

    call_command("finalize_stale_syncs")

    ms.refresh_from_db()
    assert ms.finalized is False
    assert ms.status == "IN_PLAY"


@pytest.mark.django_db
def test_incomplete_knockout_result_not_finalized(tournament, knockout_stage, teams, capsys):
    """A knockout frozen mid-live — level score, no shootout winner — must not
    be sealed as final (finalized rows are never re-fetched; this is how the
    İsviçre–Kolombiya penalties got lost). Flagged for resync_slots instead."""
    now = timezone.now()
    s = _slot(tournament, knockout_stage, teams, "R16-5", now - timedelta(hours=6))
    ms = MatchSync.objects.create(slot=s, external_id="7", status="IN_PLAY")
    ActualResult.objects.create(
        slot=s, home_score=1, away_score=1, source="API",
        went_to_extra_time=True, home_score_aet=1, away_score_aet=1,
    )

    call_command("finalize_stale_syncs")

    ms.refresh_from_db()
    assert ms.finalized is False
    assert "resync_slots R16-5" in capsys.readouterr().out


@pytest.mark.django_db
def test_complete_knockout_shootout_finalizes(tournament, knockout_stage, teams):
    """A level knockout WITH a shootout winner is a complete result → finalized."""
    now = timezone.now()
    s = _slot(tournament, knockout_stage, teams, "R16-6", now - timedelta(hours=6))
    ms = MatchSync.objects.create(slot=s, external_id="8", status="IN_PLAY")
    ActualResult.objects.create(
        slot=s, home_score=1, away_score=1, source="API",
        went_to_extra_time=True, home_score_aet=1, away_score_aet=1,
        went_to_penalties=True, home_penalties=4, away_penalties=3,
        penalty_winner=teams["TUR"],
    )

    call_command("finalize_stale_syncs")

    ms.refresh_from_db()
    assert ms.finalized is True


@pytest.mark.django_db
def test_finalizes_finished_status_not_yet_finalized(tournament, group_stage, teams):
    """FINISHED but finalized=False (e.g. a manual result) → finalized."""
    now = timezone.now()
    s = _slot(tournament, group_stage, teams, "GroupA-M2", now - timedelta(hours=3))
    ms = MatchSync.objects.create(slot=s, external_id="2", status="FINISHED")
    ActualResult.objects.create(slot=s, home_score=1, away_score=1, source="MANUAL")

    call_command("finalize_stale_syncs")

    ms.refresh_from_db()
    assert ms.finalized is True


@pytest.mark.django_db
def test_leaves_match_within_cap_untouched(tournament, group_stage, teams):
    """A genuinely in-play match (within its cap) must NOT be frozen, even though
    it already has a live running score."""
    now = timezone.now()
    s = _slot(tournament, group_stage, teams, "GroupA-M3", now - timedelta(minutes=20))
    ms = MatchSync.objects.create(slot=s, external_id="3", status="IN_PLAY")
    ActualResult.objects.create(slot=s, home_score=1, away_score=0, source="API")

    call_command("finalize_stale_syncs")

    ms.refresh_from_db()
    assert ms.finalized is False
    assert ms.status == "IN_PLAY"


@pytest.mark.django_db
def test_leaves_match_without_result_untouched(tournament, group_stage, teams):
    """Past cap but no result captured → left alone for investigation, not
    silently frozen (finalizing would stop any chance of grabbing a score)."""
    now = timezone.now()
    s = _slot(tournament, group_stage, teams, "GroupA-M4", now - timedelta(hours=3))
    ms = MatchSync.objects.create(slot=s, external_id="4", status="IN_PLAY")

    call_command("finalize_stale_syncs")

    ms.refresh_from_db()
    assert ms.finalized is False


@pytest.mark.django_db
def test_dry_run_writes_nothing(tournament, group_stage, teams):
    now = timezone.now()
    s = _slot(tournament, group_stage, teams, "GroupA-M5", now - timedelta(hours=3))
    ms = MatchSync.objects.create(slot=s, external_id="5", status="IN_PLAY")
    ActualResult.objects.create(slot=s, home_score=2, away_score=0, source="API")

    call_command("finalize_stale_syncs", "--dry-run")

    ms.refresh_from_db()
    assert ms.finalized is False
    assert ms.status == "IN_PLAY"
