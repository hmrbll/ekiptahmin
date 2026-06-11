"""Django wrapper for the pure-Python group standings calculator.

Builds standings from a user's SlotPrediction rows for one group, and
exposes helpers used by the R32 cascade form to derive "Group A 2nd"
into a concrete Team object.

Best-third allocation (R32 thirds slots): uses FIFA's official
allocation table (data/wc2026/best_third_allocation.json) to map each
combination of 8 qualifying third-placed groups to specific R32 slots.
The JSON was generated from the table on Wikipedia's
"2026 FIFA World Cup knockout stage" article.
"""

import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from apps.scoring.standings import GroupMatch, TeamStanding, compute_group_standings
from apps.tournament.models import BracketSlot, Team

from .models import SlotPrediction


_BEST_THIRD_PATH = Path(settings.BASE_DIR) / "data" / "wc2026" / "best_third_allocation.json"


@lru_cache(maxsize=1)
def _best_third_table() -> dict[str, dict[str, str]]:
    """Lazy-load the FIFA Best-Third Allocation table (495 combinations)."""
    if not _BEST_THIRD_PATH.exists():
        return {}
    return json.loads(_BEST_THIRD_PATH.read_text(encoding="utf-8"))


def predictions_to_group_matches(predictions) -> list[GroupMatch]:
    """Convert an iterable of SlotPrediction into GroupMatch dataclasses."""
    return [
        GroupMatch(
            home_team=p.home_team.code,
            away_team=p.away_team.code,
            home_score=p.home_score,
            away_score=p.away_score,
        )
        for p in predictions
    ]


def _user_predicted_matches(user, tournament, group_letter: str):
    """Return the user's most recent prediction per group slot. May be empty."""
    group_slots = list(
        BracketSlot.objects.filter(
            tournament=tournament,
            stage__kind="GROUP",
            position__startswith=f"Group{group_letter}-",
        ).values_list("id", flat=True)
    )
    if not group_slots:
        return []

    preds = (
        SlotPrediction.objects
        .filter(user=user, slot_id__in=group_slots)
        .select_related("home_team", "away_team")
        .order_by("slot_id", "-prediction_round__order")
    )
    seen: set[int] = set()
    latest = []
    for p in preds:
        if p.slot_id in seen:
            continue
        seen.add(p.slot_id)
        latest.append(p)
    return latest


def standings_for_group(
    user, tournament, group_letter: str, *, pad_with_zeros: bool = True,
) -> list[TeamStanding]:
    """Compute the standings for one group from the user's predictions.

    Uses the user's most recent prediction per slot across all rounds.
    By default, returns one row per team in the group — teams the user
    hasn't predicted yet appear with all zeros so the table is never empty.
    Pass `pad_with_zeros=False` for the cascade path: it must distinguish
    "no predictions" from "predictions that happen to leave a team at 0".
    """
    latest = _user_predicted_matches(user, tournament, group_letter)
    standings = compute_group_standings(predictions_to_group_matches(latest))

    if pad_with_zeros:
        seen_codes = {s.team_code for s in standings}
        group_teams = Team.objects.filter(
            tournament=tournament, group_letter=group_letter,
        ).exclude(code__in=seen_codes).order_by("name_tr")
        for team in group_teams:
            standings.append(TeamStanding(team_code=team.code))

    return standings


def derive_group_team(user, tournament, group_letter: str, position: int):
    """Return the Team the user has at `position` of group `group_letter`,
    or None if the user hasn't predicted enough matches to determine it.

    Uses unpadded standings — a team in slot 1 of an empty group is *not*
    a real prediction.
    """
    standings = standings_for_group(user, tournament, group_letter, pad_with_zeros=False)
    if len(standings) < position:
        return None
    target_code = standings[position - 1].team_code
    return Team.objects.filter(tournament=tournament, code=target_code).first()


def thirds_candidates(user, tournament, group_letters: list[str]) -> list[Team]:
    """For "3.lerden biri (X/Y/Z)" sources: return the user's predicted 3rd-place
    team in each of the given groups, in input order. Empty entries omitted.

    Kept for fallback/legacy callers — the canonical R32 thirds derivation
    is now `derive_best_third_for_slot` (uses the FIFA allocation table).
    """
    teams: list[Team] = []
    for letter in group_letters:
        team = derive_group_team(user, tournament, letter, 3)
        if team is not None and team not in teams:
            teams.append(team)
    return teams


def qualifying_thirds(user, tournament) -> tuple[list[str], dict[str, TeamStanding]] | tuple[None, None]:
    """Compute which 8 group letters qualify with their third-placed teams.

    Returns (sorted_letters, third_standings_by_letter) where the first list
    is the 8 group letters whose third-placed finisher advances (in
    alphabetical order — the JSON table is keyed this way), and the dict
    maps each qualifying letter to that group's third-place TeamStanding.

    Returns (None, None) if any of the 12 groups has no third-place team
    yet (i.e., the user hasn't predicted enough matches to determine all
    12 groups' standings).
    """
    third_by_letter: dict[str, TeamStanding] = {}
    for letter in "ABCDEFGHIJKL":
        s = standings_for_group(user, tournament, letter, pad_with_zeros=False)
        if len(s) < 3:
            return None, None
        third_by_letter[letter] = s[2]

    # Rank the 12 thirds by FIFA-aligned criteria: points, gd, gf, then
    # alphabetical group letter as a deterministic tiebreaker (real FIFA
    # uses fair play / draw, but those are out of scope here).
    ranked = sorted(
        third_by_letter.items(),
        key=lambda kv: (-kv[1].points, -kv[1].gd, -kv[1].gf, kv[0]),
    )
    top_8_letters = sorted(letter for letter, _ in ranked[:8])
    return top_8_letters, third_by_letter


def derive_best_third_for_slot(user, tournament, slot: BracketSlot):
    """For an R32 slot whose source is "3.lerden biri (...)" — return the
    Team that the FIFA allocation table assigns to that slot, given the
    user's group predictions. Returns None if the user hasn't predicted
    enough matches for all 12 groups to have a determinable third-place
    finisher.
    """
    qualifying, third_by_letter = qualifying_thirds(user, tournament)
    return best_third_from_qualifying(tournament, slot, qualifying, third_by_letter)


def best_third_from_qualifying(tournament, slot: BracketSlot, qualifying, third_by_letter):
    """Allocation-table lookup half of `derive_best_third_for_slot`, split out
    so callers that check many slots (cascade invalidation) can compute
    `qualifying_thirds` once and reuse it.
    """
    if qualifying is None:
        return None

    table = _best_third_table()
    key = "".join(qualifying)
    slot_to_letter = table.get(key)
    if slot_to_letter is None:
        return None

    target_letter = slot_to_letter.get(slot.position)
    if target_letter is None:
        return None

    target_code = third_by_letter[target_letter].team_code
    return Team.objects.filter(tournament=tournament, code=target_code).first()
