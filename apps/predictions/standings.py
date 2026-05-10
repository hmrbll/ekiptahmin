"""Django wrapper for the pure-Python group standings calculator.

Builds standings from a user's SlotPrediction rows for one group, and
exposes helpers used by the R32 cascade form to derive "Group A 2nd"
into a concrete Team object.
"""

from apps.scoring.standings import GroupMatch, TeamStanding, compute_group_standings
from apps.tournament.models import BracketSlot, Team

from .models import SlotPrediction


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
    """
    teams: list[Team] = []
    for letter in group_letters:
        team = derive_group_team(user, tournament, letter, 3)
        if team is not None and team not in teams:
            teams.append(team)
    return teams
