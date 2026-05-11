"""Leaderboard aggregation built on top of materialized SlotScore rows.

`leaderboard_for_tournament` is a flat aggregation — for each user it sums
SlotScore rows, computes the per-round breakdown, and the 6 tiebreaker
values defined in project_scoring_mechanic memory:

  1. Total points
  2. Weighted correct-match count (each correct match counted at the weight
     of the round in which the user first got it right)
  3. Match-only points (excluding penalty shootout bonus)
  4. First-round (Pre-turnuva, order=0) total points
  5. First-round correct-match count
  6. First-round match-only points

Two users are "truly tied" only when all six tiebreaker values are equal.
`describe_ties` produces a per-group human-readable note about which
tiebreaker resolved each near-tie — surfaced under the leaderboard table.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.contrib.auth import get_user_model

from apps.tournament.models import PredictionRound, Tournament

from .models import SlotScore

# Matchup types that count as "correct" for tiebreaker 2/5.
CORRECT_MATCHUP_TYPES = {
    SlotScore.EXACT,
    SlotScore.DIFF,
    SlotScore.RESULT,
    SlotScore.PENALTY_LOSER_BONUS,
}

# Display labels for tie-explanation strings (in TR per UI convention).
TIEBREAKER_LABELS = [
    "toplam puan",
    "ağırlıklı doğru maç sayısı",
    "sadece maç puanı (penaltı bonusu hariç)",
    "1. tur toplam puan",
    "1. tur doğru maç sayısı",
    "1. tur sadece maç puanı",
]


@dataclass
class LeaderboardEntry:
    user: "object"  # User instance (avoid importing the model here)
    rank: int
    total: Decimal
    per_round: dict  # {round.order: Decimal} for every round in the tournament
    counts: dict  # {SlotScore.matchup_type: int}
    tiebreakers: tuple  # (total, weighted_correct, match_only, fr_total, fr_correct, fr_match_only)
    nickname: str = ""  # cached for template use


def leaderboard_for_tournament(tournament: Tournament) -> list[LeaderboardEntry]:
    """Build the ranked leaderboard from materialized SlotScore rows.

    Returns one entry per user who has at least one SlotScore row for
    `tournament`. Rank is competition-style: identical-tiebreaker users
    share a rank, the next jumps accordingly.
    """
    User = get_user_model()

    rounds = list(PredictionRound.objects.filter(tournament=tournament).order_by("order"))
    round_orders = [r.order for r in rounds]
    weight_by_order = {r.order: r.weight for r in rounds}
    first_round_order = rounds[0].order if rounds else 0

    # All users with at least one score row in this tournament.
    user_ids = list(
        SlotScore.objects
        .filter(slot__tournament=tournament)
        .values_list("user_id", flat=True)
        .distinct()
    )
    users = {u.id: u for u in User.objects.filter(id__in=user_ids)}

    entries: list[LeaderboardEntry] = []
    for uid, user in users.items():
        scores = list(
            SlotScore.objects
            .filter(user_id=uid, slot__tournament=tournament)
            .only("total", "points_match", "matchup_type", "earning_round_order")
        )

        total = sum((s.total for s in scores), Decimal("0"))
        match_only = sum((s.points_match for s in scores), Decimal("0"))

        per_round = {o: Decimal("0") for o in round_orders}
        weighted_correct = Decimal("0")
        counts: dict[str, int] = {}
        for s in scores:
            counts[s.matchup_type] = counts.get(s.matchup_type, 0) + 1
            if s.earning_round_order is not None and s.earning_round_order in per_round:
                per_round[s.earning_round_order] += s.total
            if s.matchup_type in CORRECT_MATCHUP_TYPES and s.earning_round_order is not None:
                weighted_correct += weight_by_order.get(s.earning_round_order, Decimal("0"))

        fr_scores = [s for s in scores if s.earning_round_order == first_round_order]
        fr_total = sum((s.total for s in fr_scores), Decimal("0"))
        fr_match_only = sum((s.points_match for s in fr_scores), Decimal("0"))
        fr_correct = Decimal(sum(
            1 for s in fr_scores if s.matchup_type in CORRECT_MATCHUP_TYPES
        ))

        entries.append(LeaderboardEntry(
            user=user,
            rank=0,  # filled in below
            total=total,
            per_round=per_round,
            counts=counts,
            tiebreakers=(total, weighted_correct, match_only, fr_total, fr_correct, fr_match_only),
            nickname=getattr(user, "nickname", "") or user.email,
        ))

    # Sort: higher tiebreaker values rank first. Negate when building the key.
    entries.sort(key=lambda e: tuple(-v for v in e.tiebreakers))

    # Competition ranking: identical tiebreaker tuples share a rank;
    # the next non-identical entry's rank reflects how many came before.
    prev_tb: Optional[tuple] = None
    prev_rank = 0
    for idx, e in enumerate(entries, start=1):
        if e.tiebreakers == prev_tb:
            e.rank = prev_rank
        else:
            e.rank = idx
            prev_rank = idx
            prev_tb = e.tiebreakers
    return entries


def describe_ties(entries: list[LeaderboardEntry]) -> list[str]:
    """For each cluster of users with the same `total`, describe how the
    tiebreaker chain (if any) ordered them. Returns one TR-language note
    per cluster — empty list if every user has a unique total.

    Skipped:
    - Clusters of exactly one user (no tie to explain).
    - Clusters where every member has identical tiebreakers (truly tied —
      no note needed; the shared rank already conveys it).
    """
    notes: list[str] = []
    i = 0
    n = len(entries)
    while i < n:
        j = i + 1
        # Group by identical total.
        while j < n and entries[j].total == entries[i].total:
            j += 1
        group = entries[i:j]
        i = j
        if len(group) < 2:
            continue

        # Walk consecutive pairs in the group. Find the first tiebreaker
        # index where they differ — that's what decided their order.
        differing_indices: list[int] = []
        for a, b in zip(group, group[1:]):
            for idx in range(1, 6):  # idx 0 is total; group is already total-equal
                if a.tiebreakers[idx] != b.tiebreakers[idx]:
                    differing_indices.append(idx)
                    break

        if not differing_indices:
            # Truly tied — same total AND same downstream tiebreakers.
            names = ", ".join(e.nickname for e in group)
            notes.append(
                f"{names}: {group[0].total} puanla eşit — tüm kriterlerde de eşit, ortak sıra."
            )
            continue

        # Use the earliest decisive tiebreaker as the explanation anchor.
        decisive_idx = min(differing_indices)
        names = ", ".join(e.nickname for e in group)
        notes.append(
            f"{names}: {group[0].total} puanla eşit — "
            f"sıra {TIEBREAKER_LABELS[decisive_idx]} kriteriyle belirlendi."
        )
    return notes
