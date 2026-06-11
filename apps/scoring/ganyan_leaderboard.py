"""Public leaderboard built on GanyanScore.

Tiebreaker chain (per docs/scoring-ganyan.md):
1. Total points (desc)
2. Exact-score hits (weighted by effective-round weight) (desc)
3. Diff hits (weighted) (desc)
4. Result hits (weighted) (desc)
5. Wrong-prediction count (asc — fewer 0-point predictions ranks higher)

Users tied on all five share a rank. Among them the display order is
alphabetical by nickname — a stable, meaning-free fallback. The tie notes
shown on the leaderboard always name the decisive criterion and each user's
value on it (never a vague "decided elsewhere" phrasing).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.contrib.auth import get_user_model
from django.utils.formats import number_format

from apps.tournament.models import PredictionRound, Tournament

from .models import GanyanScore

# Outcome tiers that count as "got something right" (for hit-count tiebreakers).
EXACT_OUTCOMES = {GanyanScore.EXACT}
DIFF_OUTCOMES = {GanyanScore.EXACT, GanyanScore.DIFF}
RESULT_OUTCOMES = {GanyanScore.EXACT, GanyanScore.DIFF, GanyanScore.RESULT}

# Display labels for tie-explanation strings (TR per UI convention).
TIEBREAKER_LABELS = [
    "toplam puan",
    "ağırlıklı tam skor sayısı",
    "ağırlıklı doğru fark sayısı",
    "ağırlıklı doğru sonuç sayısı",
    "az yanlış (sıfır puanlı maç)",
]


@dataclass
class GanyanLeaderboardEntry:
    user: "object"
    rank: int
    total: Decimal
    counts: dict   # {outcome: int}  — best-tier-achieved buckets
    score_breakdown: dict  # {"exact": Decimal, "diff": Decimal, "result": Decimal, "penalty": Decimal}
    tiebreakers: tuple
    nickname: str = ""


def leaderboard_for_tournament(tournament: Tournament) -> list[GanyanLeaderboardEntry]:
    User = get_user_model()

    rounds = list(PredictionRound.objects.filter(tournament=tournament).order_by("order"))
    weight_by_id = {r.id: r.weight for r in rounds}

    user_ids = list(
        GanyanScore.objects
        .filter(slot__tournament=tournament)
        .values_list("user_id", flat=True)
        .distinct()
    )
    users = {u.id: u for u in User.objects.filter(id__in=user_ids)}

    entries: list[GanyanLeaderboardEntry] = []
    for uid, user in users.items():
        scores = list(
            GanyanScore.objects
            .filter(user_id=uid, slot__tournament=tournament)
            .only(
                "total", "score_exact", "score_diff", "score_result", "score_penalty",
                "outcome", "effective_round_id", "wrong_count_contribution",
            )
        )

        total = sum((s.total for s in scores), Decimal("0"))
        sum_exact = sum((s.score_exact for s in scores), Decimal("0"))
        sum_diff = sum((s.score_diff for s in scores), Decimal("0"))
        sum_result = sum((s.score_result for s in scores), Decimal("0"))
        sum_penalty = sum((s.score_penalty for s in scores), Decimal("0"))

        # Tier counts: each match contributes to exactly one bucket (its outcome).
        counts: dict[str, int] = {}
        # Weighted hit counts for tiebreaker layers 2-4.
        weighted_exact = Decimal("0")
        weighted_diff = Decimal("0")
        weighted_result = Decimal("0")
        wrong = 0
        for s in scores:
            counts[s.outcome] = counts.get(s.outcome, 0) + 1
            weight = weight_by_id.get(s.effective_round_id, Decimal("0"))
            if s.outcome in EXACT_OUTCOMES:
                weighted_exact += weight
            if s.outcome in DIFF_OUTCOMES:
                weighted_diff += weight
            if s.outcome in RESULT_OUTCOMES:
                weighted_result += weight
            wrong += s.wrong_count_contribution

        entries.append(GanyanLeaderboardEntry(
            user=user,
            rank=0,
            total=total,
            counts=counts,
            score_breakdown={
                "exact": sum_exact,
                "diff": sum_diff,
                "result": sum_result,
                "penalty": sum_penalty,
            },
            # Layers 1-4: higher = better → negate for ascending sort key.
            # Layer 5: lower wrong = better → already ascending.
            tiebreakers=(
                -total,
                -weighted_exact,
                -weighted_diff,
                -weighted_result,
                wrong,
            ),
            nickname=getattr(user, "nickname", "") or user.email,
        ))

    # Sort by the ranked tiebreakers, then alphabetically by nickname as a
    # stable display fallback. Nickname is NOT part of the rank key below, so
    # users equal on all five criteria still share a rank.
    entries.sort(key=lambda e: (e.tiebreakers, e.nickname.lower()))

    # Competition ranking: identical tiebreaker tuples share a rank.
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


def _format_tiebreaker_value(value) -> str:
    """Localized display for one tiebreaker value. Weighted hit counts are
    Decimal (round weights), wrong count is a plain int."""
    if isinstance(value, Decimal):
        return number_format(value, decimal_pos=2)
    return str(value)


def _tiebreaker_value(entry: GanyanLeaderboardEntry, idx: int):
    """Un-negate the sort key back to the user-facing value for layer `idx`
    (layers 1-3 are stored negated for ascending sort; layer 4 isn't)."""
    raw = entry.tiebreakers[idx]
    return -raw if idx < 4 else raw


def describe_ties(entries: list[GanyanLeaderboardEntry]) -> list[str]:
    """For each cluster with identical total, describe which tiebreaker decided
    their order — naming the criterion AND each user's value on it, so the
    note reads "decided by X: A 3,40 · B 2,55" rather than something vague.
    Returns one TR-language note per cluster, or empty list when every user
    has a unique total.
    """
    notes: list[str] = []
    i = 0
    n = len(entries)
    while i < n:
        j = i + 1
        while j < n and entries[j].total == entries[i].total:
            j += 1
        group = entries[i:j]
        i = j
        if len(group) < 2:
            continue

        names = ", ".join(e.nickname for e in group)
        total_disp = number_format(group[0].total, decimal_pos=2)

        differing_indices: list[int] = []
        for a, b in zip(group, group[1:]):
            for idx in range(1, 5):  # idx 0 is total; group is already total-equal
                if a.tiebreakers[idx] != b.tiebreakers[idx]:
                    differing_indices.append(idx)
                    break

        if not differing_indices:
            criteria = ", ".join(TIEBREAKER_LABELS[1:])
            notes.append(
                f"{names}: {total_disp} puanla eşit ve dört eşitlik kriterinde de "
                f"({criteria}) aynı değerdeler — aynı sırayı paylaşıyorlar, "
                f"isimler alfabetik dizildi."
            )
            continue

        decisive_idx = min(differing_indices)
        values = " · ".join(
            f"{e.nickname} {_format_tiebreaker_value(_tiebreaker_value(e, decisive_idx))}"
            for e in group
        )
        notes.append(
            f"{names}: {total_disp} puanla eşit — sırayı "
            f"{TIEBREAKER_LABELS[decisive_idx]} kriteri belirledi: {values}."
        )
    return notes
