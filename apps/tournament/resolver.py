"""Resolve the *actual* bracket from recorded results.

Our code owns the tournament tree — the live API only supplies match scores.
This module turns those scores into team assignments:

1. As each group finishes, its 1st/2nd feed their R32 slots; once all 12 groups
   are done, the 8 best third-placed teams fill the "thirds" R32 slots via
   FIFA's official allocation table (data/wc2026/best_third_allocation.json).
2. Each knockout result pushes its winner (or loser, for the third-place match)
   into the slot it feeds.

`resolve_bracket(tournament)` is idempotent — it recomputes every derivable
slot and saves only the ones whose teams changed. It runs on every ActualResult
save (so both the live sync and manual entry advance the bracket) and via
`manage.py resolve_bracket`. Saving a BracketSlot triggers no scoring signals,
so there's no recursion.

Winner determination mirrors the scoring rule: penalties → the shootout winner;
otherwise the higher *effective* score (120' for an ET match, else 90').
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from apps.scoring.standings import GroupMatch, compute_group_standings

from .models import ActualResult, BracketSlot, Team

GROUP_LETTERS = "ABCDEFGHIJKL"
# R32 is filled from group standings; later rounds from feeding-slot results.
_KO_FEED_STAGES = ["R16", "QF", "SF", "THIRD", "FINAL"]

_BEST_THIRD_PATH = Path(settings.BASE_DIR) / "data" / "wc2026" / "best_third_allocation.json"


@lru_cache(maxsize=1)
def _best_third_table() -> dict[str, dict[str, str]]:
    """FIFA best-third allocation: {8 sorted qualifying letters: {R32 pos: letter}}."""
    if not _BEST_THIRD_PATH.exists():
        return {}
    return json.loads(_BEST_THIRD_PATH.read_text(encoding="utf-8"))


# ---------- group standings (from actual results) ----------


def _group_matches(tournament, letter: str) -> tuple[list[GroupMatch], int, int]:
    """Return (matches, slot_count, result_count) for one group from actuals."""
    slots = list(
        BracketSlot.objects
        .filter(tournament=tournament, stage__kind="GROUP",
                position__startswith=f"Group{letter}-")
        .select_related("home_team_actual", "away_team_actual", "result")
    )
    matches: list[GroupMatch] = []
    result_count = 0
    for s in slots:
        ar = getattr(s, "result", None)
        if ar is None:
            continue
        result_count += 1
        if s.home_team_actual_id and s.away_team_actual_id:
            matches.append(GroupMatch(
                home_team=s.home_team_actual.code,
                away_team=s.away_team_actual.code,
                home_score=ar.home_score,   # group matches never go to ET → 90' == final
                away_score=ar.away_score,
            ))
    return matches, len(slots), result_count


# ---------- knockout winner / loser ----------


def _winner_loser(slot: BracketSlot) -> tuple[Team | None, Team | None]:
    """The team that advanced from `slot` and the one that didn't, or (None, None)
    when the result/teams aren't settled enough to decide."""
    ar = ActualResult.objects.filter(slot=slot).select_related("penalty_winner").first()
    home, away = slot.home_team_actual, slot.away_team_actual
    if ar is None or home is None or away is None:
        return None, None
    if ar.went_to_penalties and ar.penalty_winner_id:
        winner = ar.penalty_winner
    else:
        eh, ea = ar.effective_home_score, ar.effective_away_score
        if eh > ea:
            winner = home
        elif ea > eh:
            winner = away
        else:
            return None, None  # undecided (e.g. ET score not recorded yet)
    loser = away if winner.id == home.id else home
    return winner, loser


# ---------- resolution passes ----------


def _resolve_r32(tournament) -> list[BracketSlot]:
    group_data = {ltr: _group_matches(tournament, ltr) for ltr in GROUP_LETTERS}
    standings = {ltr: compute_group_standings(group_data[ltr][0]) for ltr in GROUP_LETTERS}
    complete = {
        ltr for ltr in GROUP_LETTERS
        if group_data[ltr][1] > 0 and group_data[ltr][1] == group_data[ltr][2]
    }

    # Best-third allocation needs every group finished.
    slot_to_letter: dict[str, str] = {}
    third_by_letter = {ltr: standings[ltr][2] for ltr in complete if len(standings[ltr]) >= 3}
    if len(complete) == len(GROUP_LETTERS) and len(third_by_letter) == len(GROUP_LETTERS):
        ranked = sorted(
            third_by_letter.items(),
            key=lambda kv: (-kv[1].points, -kv[1].gd, -kv[1].gf, kv[0]),
        )
        qualifying = sorted(ltr for ltr, _ in ranked[:8])
        slot_to_letter = _best_third_table().get("".join(qualifying), {})

    teams_by_code = {t.code.upper(): t for t in Team.objects.filter(tournament=tournament)}

    changed: list[BracketSlot] = []
    for slot in BracketSlot.objects.filter(tournament=tournament, stage__kind="R32"):
        dirty = False
        for side in ("home", "away"):
            team = _r32_side_team(slot, side, standings, complete,
                                  slot_to_letter, third_by_letter, teams_by_code)
            if team and getattr(slot, f"{side}_team_actual_id") != team.id:
                setattr(slot, f"{side}_team_actual", team)
                dirty = True
        if dirty:
            slot.save(update_fields=["home_team_actual", "away_team_actual"])
            changed.append(slot)
    return changed


def _r32_side_team(slot, side, standings, complete, slot_to_letter, third_by_letter, teams_by_code):
    letter = getattr(slot, f"{side}_source_group_letter")
    position = getattr(slot, f"{side}_source_group_position")
    if letter and position:
        if letter not in complete:
            return None
        s = standings.get(letter, [])
        if len(s) >= position:
            return teams_by_code.get(s[position - 1].team_code.upper())
        return None

    if getattr(slot, f"{side}_source_thirds_groups"):
        target_letter = slot_to_letter.get(slot.position)
        if target_letter and target_letter in third_by_letter:
            return teams_by_code.get(third_by_letter[target_letter].team_code.upper())
    return None


def _resolve_ko_feeds(tournament) -> list[BracketSlot]:
    changed: list[BracketSlot] = []
    for kind in _KO_FEED_STAGES:
        slots = (
            BracketSlot.objects
            .filter(tournament=tournament, stage__kind=kind)
            .select_related(
                "home_source_slot__home_team_actual", "home_source_slot__away_team_actual",
                "away_source_slot__home_team_actual", "away_source_slot__away_team_actual",
            )
        )
        for slot in slots:
            dirty = False
            for side in ("home", "away"):
                source = getattr(slot, f"{side}_source_slot")
                if source is None:
                    continue
                winner, loser = _winner_loser(source)
                team = winner if getattr(slot, f"{side}_source_kind") == BracketSlot.SOURCE_KIND_WINNER else loser
                if team and getattr(slot, f"{side}_team_actual_id") != team.id:
                    setattr(slot, f"{side}_team_actual", team)
                    dirty = True
            if dirty:
                slot.save(update_fields=["home_team_actual", "away_team_actual"])
                changed.append(slot)
    return changed


def resolve_bracket(tournament) -> list[BracketSlot]:
    """Fill every derivable slot's teams from current results. Idempotent.

    Returns the slots whose teams changed (for logging/tests). Safe to call on
    every result save — cheap while the group stage is incomplete (no R32 slot
    resolves until its source group is done).
    """
    if tournament is None:
        return []
    changed = _resolve_r32(tournament)
    changed += _resolve_ko_feeds(tournament)
    return changed
