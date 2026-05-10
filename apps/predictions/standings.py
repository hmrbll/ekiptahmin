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


def standings_for_group(user, tournament, group_letter: str) -> list[TeamStanding]:
    """Compute the standings for one group from the user's predictions.

    Uses the user's most recent prediction per slot across all rounds.
    Returns standings sorted 1st → last; teams the user hasn't predicted
    yet won't appear.
    """
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
    # Take latest per slot
    seen: set[int] = set()
    latest = []
    for p in preds:
        if p.slot_id in seen:
            continue
        seen.add(p.slot_id)
        latest.append(p)

    return compute_group_standings(predictions_to_group_matches(latest))


def derive_group_team(user, tournament, group_letter: str, position: int):
    """Return the Team object the user has at `position` of group `group_letter`,
    or None if not enough predictions exist."""
    standings = standings_for_group(user, tournament, group_letter)
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
