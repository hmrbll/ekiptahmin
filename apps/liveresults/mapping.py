"""Match a football-data match payload to one of our BracketSlots.

The provider has no concept of our `position` ids, so we anchor on the two
things both sides agree on: the team pair and the kickoff date. This is used
read-only by `fd_probe` (Step 1) and to seed `MatchSync.external_id` by
`map_external_ids` (Step 2).

Team identity: football-data exposes a 3-letter `tla` per team, which usually
(but not always) equals our FIFA `Team.code`. Known divergences are calibrated
in TLA_OVERRIDES once we see real payloads with a live key.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

from apps.tournament.models import BracketSlot, Tournament

# football-data `tla` -> our FIFA Team.code, for the cases where they differ.
# Empty until calibrated against real payloads (Step 1 handoff). Keys/values
# are compared uppercased.
TLA_OVERRIDES: dict[str, str] = {}


def normalize_tla(tla: str | None) -> str:
    if not tla:
        return ""
    tla = tla.strip().upper()
    return TLA_OVERRIDES.get(tla, tla)


def extract_team_codes(match: dict) -> tuple[str, str]:
    """Return (home_code, away_code) from a match payload, normalized/uppercased."""
    home = normalize_tla((match.get("homeTeam") or {}).get("tla"))
    away = normalize_tla((match.get("awayTeam") or {}).get("tla"))
    return home, away


def match_utc_date(match: dict):
    """Parse the match's UTC kickoff into a timezone-aware datetime, or None."""
    raw = match.get("utcDate")
    if not raw:
        return None
    try:
        # football-data uses e.g. "2026-06-11T19:00:00Z"
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(dt_timezone.utc)
    except (ValueError, AttributeError):
        return None


def find_slot_for_match(
    tournament: Tournament,
    match: dict,
    slots: list[BracketSlot] | None = None,
) -> BracketSlot | None:
    """Find the BracketSlot a football-data match payload refers to.

    Primary key is the unordered team-code pair (a given pair meets at most
    once in the group stage). When several slots share a pair (rare, knockout),
    the one whose kickoff is closest to the payload's date wins.

    Returns None when either team code is unknown to us or no slot has both
    teams assigned yet (e.g. an unresolved knockout slot).
    """
    home_code, away_code = extract_team_codes(match)
    if not home_code or not away_code:
        return None
    pair = frozenset((home_code, away_code))

    if slots is None:
        slots = list(
            BracketSlot.objects
            .filter(tournament=tournament,
                    home_team_actual__isnull=False,
                    away_team_actual__isnull=False)
            .select_related("home_team_actual", "away_team_actual")
        )

    candidates = [
        s for s in slots
        if frozenset((s.home_team_actual.code.upper(), s.away_team_actual.code.upper())) == pair
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    kickoff = match_utc_date(match)
    if kickoff is None:
        return candidates[0]
    return min(candidates, key=lambda s: abs((s.scheduled_kickoff - kickoff).total_seconds()))
