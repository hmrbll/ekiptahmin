"""Core live-result sync + the throttled web trigger.

`sync_live_matches()` is the single entry point both the management command and
the homepage trigger call. It:
  1. finds mapped, not-yet-finalized slots in the live window (cheap; if none,
     it returns WITHOUT calling the API — this is what keeps a forgotten open
     tab from hammering football-data outside match windows),
  2. fetches that day's matches in one request,
  3. writes/updates ActualResult (source=API) for slots whose teams are known,
  4. updates MatchSync (status/minute), and flags `finalized` on FINISHED so the
     match is never requested again.

Teams are never written here — per the design, the bracket tree is resolved by
our own code (apps/liveresults/resolver — Step 3b). A knockout slot without
teams yet gets its status tracked but no score until the resolver fills it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from apps.tournament.models import ActualResult, BracketSlot, Tournament

from .client import FootballDataClient, FootballDataError
from .models import MatchSync
from .score import ScoreMappingError, map_score

# Polling starts 15 min before kickoff and runs until the match is FINISHED
# (MatchSync.finalized — the primary stop). As a fallback when no FINISHED ever
# arrives (manual entry / API gap), a match also stops counting as live once
# we're past its expected end + 30 min grace. Knockouts get longer because they
# can run to extra time + penalties.
LIVE_LEAD = timedelta(minutes=15)
_LIVE_CAP_MINUTES = {"GROUP": 140}   # ~110' play + 30' grace
_LIVE_CAP_DEFAULT = 180              # knockout: up to ET + penalties + grace


def live_cap(stage_kind: str) -> timedelta:
    """How long after kickoff a match can still count as live without a FINISHED
    signal. Knockouts get the longer cap (extra time + penalties)."""
    return timedelta(minutes=_LIVE_CAP_MINUTES.get(stage_kind, _LIVE_CAP_DEFAULT))

# Web trigger throttle: at most one external sync per this many seconds.
SYNC_THROTTLE_SECONDS = 45
_CACHE_LAST = "liveresults:last_sync_ts"
_CACHE_LOCK = "liveresults:sync_lock"

# ActualResult fields the sync owns, for change-detection.
_RESULT_FIELDS = (
    "home_score", "away_score", "went_to_extra_time", "went_to_penalties",
    "home_penalties", "away_penalties", "home_score_aet", "away_score_aet",
)


@dataclass
class SyncReport:
    fetched: int = 0
    written: int = 0
    unchanged: int = 0
    finalized: int = 0
    no_score_yet: int = 0
    awaiting_teams: int = 0
    unmatched: int = 0
    errors: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)  # human-readable per-slot trace

    def summary(self) -> str:
        return (
            f"fetched {self.fetched} | written {self.written} | unchanged {self.unchanged} | "
            f"finalized {self.finalized} | no-score {self.no_score_yet} | "
            f"awaiting-teams {self.awaiting_teams} | unmatched {self.unmatched} | "
            f"errors {len(self.errors)}"
        )


def slots_in_live_window(tournament: Tournament, now=None):
    """Mapped, not-finalized slots from 15 min before kickoff until their
    per-stage live cap (the FINISHED flag is the usual earlier stop)."""
    now = now or timezone.now()
    candidates = (
        BracketSlot.objects
        .filter(
            tournament=tournament,
            scheduled_kickoff__lte=now + LIVE_LEAD,
            scheduled_kickoff__gte=now - timedelta(minutes=_LIVE_CAP_DEFAULT),
            live_sync__finalized=False,
        )
        .exclude(live_sync__external_id="")
        .select_related("live_sync", "home_team_actual", "away_team_actual", "stage")
        .order_by("scheduled_kickoff")
    )
    return [s for s in candidates if now <= s.scheduled_kickoff + live_cap(s.stage.kind)]


def live_syncs(tournament: Tournament, now=None) -> list[MatchSync]:
    """MatchSync rows for matches that are *currently* live, kickoff-ordered.

    "Currently live" = provider status IN_PLAY/PAUSED, not finalized, AND still
    within the per-stage `live_cap`. A match stuck IN_PLAY past its cap (FINISHED
    never arrived — API gap or a manually entered result) is NOT live: it has
    dropped out of the poller's window and belongs in "recent results" instead.

    Single source of truth shared by the homepage live module (which renders
    these rows) and the recent-results list (which excludes their slot ids). The
    two MUST agree on the cap, otherwise a capped-out match falls into the gap —
    hidden from the live module yet still treated as "live" by recent results,
    so it shows nowhere even though it has a scored ActualResult.
    """
    now = now or timezone.now()
    rows = (
        MatchSync.objects
        .filter(slot__tournament=tournament,
                status__in=MatchSync.LIVE_STATUSES, finalized=False)
        .select_related("slot__stage", "slot__home_team_actual", "slot__away_team_actual")
        .order_by("slot__scheduled_kickoff")
    )
    return [ms for ms in rows if now <= ms.slot.scheduled_kickoff + live_cap(ms.slot.stage.kind)]


def _penalty_winner_team(slot: BracketSlot, side: str | None):
    if side == "HOME":
        return slot.home_team_actual
    if side == "AWAY":
        return slot.away_team_actual
    return None


def _result_changed(existing: ActualResult | None, fields: dict, pw_team) -> bool:
    if existing is None:
        return True
    if existing.penalty_winner_id != (pw_team.id if pw_team else None):
        return True
    return any(getattr(existing, f) != fields[f] for f in _RESULT_FIELDS)


def sync_live_matches(tournament: Tournament | None = None, *, dry_run: bool = False, now=None) -> SyncReport:
    """Pull and persist live results for the active tournament. See module docstring."""
    report = SyncReport()
    now = now or timezone.now()

    if tournament is None:
        tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        report.errors.append("No active tournament.")
        return report

    slots = slots_in_live_window(tournament, now=now)
    if not slots:
        return report  # nothing live → no API call (the idle-cheap path)

    client = FootballDataClient()
    if not client.is_configured:
        report.errors.append("FOOTBALL_DATA_API_KEY not set.")
        return report

    date_from = (now - timedelta(minutes=_LIVE_CAP_DEFAULT)).date().isoformat()
    date_to = (now + LIVE_LEAD).date().isoformat()
    try:
        matches = client.get_competition_matches(date_from=date_from, date_to=date_to)
    except FootballDataError as exc:
        report.errors.append(str(exc))
        return report

    by_id = {str(m.get("id")): m for m in matches}
    report.fetched = len(matches)

    for slot in slots:
        msync: MatchSync = slot.live_sync
        payload = by_id.get(msync.external_id)
        if payload is None:
            report.unmatched += 1
            report.lines.append(f"{slot.position}: external {msync.external_id} not in window fetch")
            continue

        status = payload.get("status") or ""
        minute = payload.get("minute")
        injury = payload.get("injuryTime")
        score = payload.get("score") or {}

        try:
            fields = map_score(score)
        except ScoreMappingError as exc:
            report.errors.append(f"{slot.position}: {exc}")
            continue

        # Always track provider status/minute (drives the live badge), even when
        # there's no writable score yet.
        if not dry_run:
            msync.status = status
            msync.minute = minute if isinstance(minute, int) else None
            msync.injury_time = injury if isinstance(injury, int) else None
            msync.last_synced_at = now

        if fields is None:
            report.no_score_yet += 1
            report.lines.append(f"{slot.position}: {status} (no score yet)")
            if not dry_run:
                msync.save(update_fields=["status", "minute", "injury_time", "last_synced_at"])
            continue

        if not (slot.home_team_actual_id and slot.away_team_actual_id):
            # Knockout slot whose teams the resolver hasn't filled yet.
            report.awaiting_teams += 1
            report.lines.append(
                f"{slot.position}: {status} {fields['home_score']}-{fields['away_score']} "
                "(awaiting team resolution)"
            )
            if not dry_run:
                msync.save(update_fields=["status", "minute", "injury_time", "last_synced_at"])
            continue

        pw_team = _penalty_winner_team(slot, fields["penalty_winner_side"])
        existing = ActualResult.objects.filter(slot=slot).first()
        changed = _result_changed(existing, fields, pw_team)
        is_finished = status == MatchSync.STATUS_FINISHED

        scoreline = f"{fields['home_score']}-{fields['away_score']}"
        if fields["went_to_penalties"]:
            scoreline += f" (pen {fields['home_penalties']}-{fields['away_penalties']})"

        if dry_run:
            report.lines.append(
                f"{slot.position}: {status} {scoreline}"
                + ("" if changed else " [unchanged]")
                + (" [→finalize]" if is_finished else "")
            )
            if changed:
                report.written += 1
            else:
                report.unchanged += 1
            if is_finished:
                report.finalized += 1
            continue

        if changed:
            ActualResult.objects.update_or_create(
                slot=slot,
                defaults={
                    **{f: fields[f] for f in _RESULT_FIELDS},
                    "penalty_winner": pw_team,
                    "source": ActualResult.SOURCE_API,
                },
            )
            report.written += 1
            report.lines.append(f"{slot.position}: wrote {status} {scoreline}")
        else:
            report.unchanged += 1

        msync.finalized = is_finished
        update_fields = ["status", "minute", "injury_time", "last_synced_at"]
        if is_finished:
            update_fields.append("finalized")
            report.finalized += 1
        msync.save(update_fields=update_fields)

    return report


def maybe_sync_live() -> None:
    """Best-effort, throttled refresh — safe to call on every homepage poll.

    Throttle + cache lock keep it to one external sync per SYNC_THROTTLE_SECONDS
    no matter how many visitors are polling. The heavy lifting (and the decision
    to call the API at all) lives in sync_live_matches, which no-ops when nothing
    is in the live window. Single-web-instance assumption: the throttle is
    per-process (fine at our scale; revisit if we scale web horizontally).
    """
    client = FootballDataClient()
    if not client.is_configured:
        return

    last = cache.get(_CACHE_LAST)
    if last is not None:
        return  # within the throttle window (key TTL == throttle seconds)

    # Claim the throttle window up front so concurrent pollers back off even
    # while the sync is still running.
    cache.set(_CACHE_LAST, timezone.now().timestamp(), timeout=SYNC_THROTTLE_SECONDS)

    if not cache.add(_CACHE_LOCK, 1, timeout=30):
        return  # another request is mid-sync
    try:
        sync_live_matches()
    finally:
        cache.delete(_CACHE_LOCK)
