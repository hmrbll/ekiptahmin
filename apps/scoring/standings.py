"""Pure-Python group standings calculator.

Operates on plain dataclasses — no Django/ORM dependency. Used to derive
group-stage standings from a user's group match predictions, so the R32
cascade can pre-fill "A Grubu 2.si" with the team the user predicted to
finish second in Group A.

Tie-break order (FIFA-aligned subset, simplified for MVP):
1. Total points (W=3, D=1, L=0)
2. Goal difference (gf - ga)
3. Goals for (gf)
4. Head-to-head points among tied teams
5. Head-to-head goal difference among tied teams
6. Alphabetical team code (deterministic fallback; FIFA uses fair play / draw)
"""

from dataclasses import dataclass
from typing import Iterable


@dataclass
class GroupMatch:
    """One group-stage match with both teams and the (predicted or actual) score."""

    home_team: str  # FIFA 3-letter code
    away_team: str
    home_score: int
    away_score: int


@dataclass
class TeamStanding:
    team_code: str
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    gf: int = 0
    ga: int = 0

    @property
    def points(self) -> int:
        return self.wins * 3 + self.draws

    @property
    def gd(self) -> int:
        return self.gf - self.ga


def _apply_match(table: dict[str, TeamStanding], m: GroupMatch) -> None:
    home = table.setdefault(m.home_team, TeamStanding(m.home_team))
    away = table.setdefault(m.away_team, TeamStanding(m.away_team))
    home.played += 1
    away.played += 1
    home.gf += m.home_score
    home.ga += m.away_score
    away.gf += m.away_score
    away.ga += m.home_score
    if m.home_score > m.away_score:
        home.wins += 1
        away.losses += 1
    elif m.home_score < m.away_score:
        away.wins += 1
        home.losses += 1
    else:
        home.draws += 1
        away.draws += 1


def _head_to_head_subtable(
    matches: list[GroupMatch], team_codes: set[str],
) -> dict[str, TeamStanding]:
    """Compute a sub-table containing only matches between the given teams."""
    sub: dict[str, TeamStanding] = {}
    for m in matches:
        if m.home_team in team_codes and m.away_team in team_codes:
            _apply_match(sub, m)
    return sub


def _sort_key(s: TeamStanding) -> tuple:
    return (-s.points, -s.gd, -s.gf, s.team_code)


def compute_group_standings(matches: Iterable[GroupMatch]) -> list[TeamStanding]:
    """Compute the standings for one group from its match predictions.

    `matches` should contain only matches within a single group. Returns
    the standings sorted from 1st to last place.
    """
    matches = list(matches)
    table: dict[str, TeamStanding] = {}
    for m in matches:
        _apply_match(table, m)

    standings = list(table.values())
    standings.sort(key=_sort_key)

    # Apply head-to-head tiebreaker for runs of teams tied on
    # (points, gd, gf). Within each tied run, re-sort by H2H.
    if len(standings) < 2:
        return standings

    out: list[TeamStanding] = []
    i = 0
    while i < len(standings):
        j = i + 1
        while (
            j < len(standings)
            and standings[j].points == standings[i].points
            and standings[j].gd == standings[i].gd
            and standings[j].gf == standings[i].gf
        ):
            j += 1
        if j - i > 1:
            tied = standings[i:j]
            sub = _head_to_head_subtable(matches, {s.team_code for s in tied})
            tied.sort(key=lambda s: _sort_key(sub.get(s.team_code, TeamStanding(s.team_code))))
            out.extend(tied)
        else:
            out.append(standings[i])
        i = j

    return out
