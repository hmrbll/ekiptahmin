"""Public scoring views (active ganyan engine).

Three routes:
- `/leaderboard/`              — overall ranked board (GanyanScore + new tiebreakers)
- `/leaderboard/<user_id>/`    — per-user score sheet
- `/results/`                  — played-matches log with ganyan payouts
- `/matches/<slot_id>/`        — single-match detail + ganyan tablosu

Legacy bracket views (SlotScore) live under /legacy/ — see `legacy_views.py`.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.predictions.models import SlotPrediction
from apps.tournament.models import (
    ActualResult,
    BracketSlot,
    Tournament,
)
from apps.tournament.sections import group_matches_into_sections

from .ganyan_leaderboard import describe_ties, leaderboard_for_tournament
from .models import GanyanScore, MatchPool


_STAGE_ORDER = ["GROUP", "R32", "R16", "QF", "SF", "THIRD", "FINAL"]

# Outcome → (TR label, Tailwind badge classes) for chip/badge rendering.
# Tier colours must match the leaderboard pills and the inline badge logic in
# user_detail.html / _prediction_chip.html (exact=primary, diff=success,
# result=warning, penalty=accent, miss=danger).
_OUTCOME_BADGE = {
    GanyanScore.EXACT: ("Tam skor", "bg-primary/10 border-primary/30 text-primary"),
    GanyanScore.DIFF: ("Aynı fark", "bg-success/10 border-success/30 text-success"),
    GanyanScore.RESULT: ("Doğru sonuç", "bg-warning/10 border-warning/30 text-warning"),
    GanyanScore.PENALTY: ("Penaltı", "bg-accent/10 border-accent/30 text-accent"),
    GanyanScore.MISS: ("Yanlış", "bg-danger/10 border-danger/30 text-danger"),
    GanyanScore.NO_PREDICTION: ("Tahmin yok", "bg-surface border-line text-fg-muted"),
}


def leaderboard(request: HttpRequest) -> HttpResponse:
    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "scoring/no_tournament.html", status=200)

    entries = leaderboard_for_tournament(tournament)
    rows = []
    for e in entries:
        c = e.counts
        # Cumulative counts: an exact score also nails the goal difference
        # and the outcome, so it counts in all three columns (mirrors the
        # weighted tiebreaker semantics in ganyan_leaderboard).
        exact = c.get(GanyanScore.EXACT, 0)
        diff_or_better = exact + c.get(GanyanScore.DIFF, 0)
        result_or_better = diff_or_better + c.get(GanyanScore.RESULT, 0)
        rows.append({
            "rank": e.rank,
            "user_id": e.user.id,
            "nickname": e.nickname,
            "total": e.total,
            "exact": exact,
            "diff": diff_or_better,
            "result": result_or_better,
            "penalty": c.get(GanyanScore.PENALTY, 0),
            "wrong": c.get(GanyanScore.MISS, 0),
            # Points earned per criterion — the "Puan" face of the toggle.
            # Already cumulative by construction: an exact hit wins the
            # exact, diff AND result pools.
            "points_exact": e.score_breakdown["exact"],
            "points_diff": e.score_breakdown["diff"],
            "points_result": e.score_breakdown["result"],
            "points_penalty": e.score_breakdown["penalty"],
        })

    return render(request, "scoring/leaderboard.html", {
        "tournament": tournament,
        "rows": rows,
        "tie_notes": describe_ties(entries),
    })


def _earning_or_latest_prediction(user, slot, effective_round_id):
    """Find the SlotPrediction whose ganyan score is being shown.

    If `effective_round_id` is set, that's the one. Otherwise (miss /
    no-result / no-prediction) we surface the user's latest prediction.
    """
    qs = SlotPrediction.objects.filter(user=user, slot=slot).select_related(
        "home_team", "away_team", "penalty_winner", "prediction_round",
    )
    if effective_round_id is not None:
        result = qs.filter(prediction_round_id=effective_round_id).first()
        if result is not None:
            return result
    return qs.order_by("-prediction_round__order").first()


def user_detail(request: HttpRequest, user_id: int) -> HttpResponse:
    """Per-slot ganyan score sheet for one user."""
    User = get_user_model()
    target = get_object_or_404(User, pk=user_id)
    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "scoring/no_tournament.html", status=200)

    scores = (
        GanyanScore.objects
        .filter(user=target, slot__tournament=tournament)
        .select_related("slot__stage", "slot__home_team_actual", "slot__away_team_actual", "effective_round")
        .order_by("slot__stage__order", "slot__scheduled_kickoff")
    )

    slot_ids = [s.slot_id for s in scores]
    actuals_by_slot = {
        a.slot_id: a for a in ActualResult.objects.filter(slot_id__in=slot_ids).select_related(
            "slot__home_team_actual", "slot__away_team_actual", "penalty_winner",
        )
    }

    is_self = request.user.is_authenticated and request.user.id == target.id

    sections_by_kind: dict[str, list[dict]] = {}
    total_points = Decimal("0")
    for score in scores:
        slot = score.slot
        actual = actuals_by_slot.get(slot.id)
        is_locked = slot.is_locked or actual is not None
        raw_prediction = _earning_or_latest_prediction(target, slot, score.effective_round_id)
        prediction_visible = is_self or is_locked
        label, badge_cls = _OUTCOME_BADGE.get(score.outcome, ("", ""))
        sections_by_kind.setdefault(slot.stage.kind, []).append({
            "slot": slot,
            "actual": actual,
            "prediction": raw_prediction if prediction_visible else None,
            "prediction_hidden": (not prediction_visible) and raw_prediction is not None,
            "score": score,
            "outcome_label": label,
            "outcome_badge_cls": badge_cls,
        })
        total_points += score.total

    sections = [
        {"kind": kind, "rows": sections_by_kind[kind]}
        for kind in _STAGE_ORDER if kind in sections_by_kind
    ]
    return render(request, "scoring/user_detail.html", {
        "tournament": tournament,
        "target_user": target,
        "sections": sections,
        "total_points": total_points,
    })


def results_list(request: HttpRequest) -> HttpResponse:
    """Played matches log with ganyan payouts and per-user breakdowns."""
    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "scoring/no_tournament.html", status=200)

    actuals = list(
        ActualResult.objects
        .filter(slot__tournament=tournament)
        .select_related(
            "slot__stage", "slot__home_team_actual", "slot__away_team_actual",
            "penalty_winner",
        )
        # Chronological so each round tab reads match 1 → match 2; the round
        # grouping below preserves this order within every section.
        .order_by("slot__scheduled_kickoff")
    )

    slot_ids = [a.slot_id for a in actuals]
    score_rows = (
        GanyanScore.objects
        .filter(slot_id__in=slot_ids)
        .exclude(outcome=GanyanScore.NO_RESULT)
        .select_related("user", "effective_round")
        .order_by("-total", "user__nickname")
    )

    user_slot_pairs = {(s.user_id, s.slot_id): s for s in score_rows}
    all_preds = (
        SlotPrediction.objects
        .filter(slot_id__in=slot_ids, user_id__in={uid for uid, _ in user_slot_pairs})
        .select_related("home_team", "away_team", "prediction_round")
        .order_by("user_id", "slot_id", "-prediction_round__order")
    )
    preds_by_user_slot: dict[tuple[int, int], list] = {}
    for p in all_preds:
        preds_by_user_slot.setdefault((p.user_id, p.slot_id), []).append(p)

    def _pick_prediction(user_id, slot_id, effective_round_id):
        bucket = preds_by_user_slot.get((user_id, slot_id), [])
        if not bucket:
            return None
        if effective_round_id is not None:
            for p in bucket:
                if p.prediction_round_id == effective_round_id:
                    return p
        return bucket[0]

    scores_by_slot: dict[int, list] = {}
    for s in score_rows:
        label, badge_cls = _OUTCOME_BADGE.get(s.outcome, ("", ""))
        scores_by_slot.setdefault(s.slot_id, []).append({
            "score": s,
            "user": s.user,
            "label": label,
            "badge_cls": badge_cls,
            "prediction": _pick_prediction(s.user_id, s.slot_id, s.effective_round_id),
        })

    matches = []
    for actual in actuals:
        matches.append({
            "actual": actual,
            "slot": actual.slot,
            "scores": scores_by_slot.get(actual.slot_id, []),
        })

    sections, default_section_key = group_matches_into_sections(matches)

    return render(request, "scoring/results.html", {
        "tournament": tournament,
        "sections": sections,
        "default_section_key": default_section_key,
    })


# Criterion → TR label for the ganyan tablosu.
_CRITERION_LABEL_TR = {
    MatchPool.EXACT: "Tam skor",
    MatchPool.DIFF: "Doğru fark",
    MatchPool.RESULT: "Doğru sonuç",
    MatchPool.PENALTY_WINNER: "Penaltı turlatan",
    MatchPool.PENALTY_SCORE: "Penaltı skoru",
    MatchPool.PENALTY_DIFF: "Penaltı farkı",
}

# Penalty criteria only render once a KO match has gone to penalties.
_PENALTY_CRITERIA = (MatchPool.PENALTY_WINNER, MatchPool.PENALTY_SCORE, MatchPool.PENALTY_DIFF)

# Result direction key → TR label for the "result" criterion breakdown.
_RESULT_KEY_TR = {"H": "Ev sahibi kazanır", "A": "Deplasman kazanır", "D": "Berabere"}


def _format_breakdown(pool: MatchPool, slot: BracketSlot) -> list[dict]:
    """Build the per-prediction-value rows of the ganyan tablosu."""
    rows = []
    pool_size = pool.pool_size
    for key, count in sorted(pool.breakdown.items(), key=lambda kv: (-kv[1], kv[0])):
        # Pre-result rows have winner_count = 0 but breakdown filled in;
        # show "if this is correct → X puan" as the potential payout.
        # Post-result rows have winner_count set; only the winning key pays.
        potential = (Decimal(pool_size) / count) if count else None
        if pool.criterion == MatchPool.RESULT:
            display_key = _RESULT_KEY_TR.get(key, key)
        else:
            display_key = key
        rows.append({
            "key": key,
            "display_key": display_key,
            "count": count,
            "potential_payout": potential,
        })
    return rows


def match_detail(request: HttpRequest, slot_id: int) -> HttpResponse:
    """Single-match detail page with the full ganyan tablosu.

    Visible to anyone but the ganyan tablosu only renders after lock.
    """
    slot = get_object_or_404(
        BracketSlot.objects.select_related(
            "tournament", "stage", "home_team_actual", "away_team_actual",
        ),
        pk=slot_id,
    )
    actual = ActualResult.objects.filter(slot=slot).select_related("penalty_winner").first()
    pools = list(
        MatchPool.objects.filter(slot=slot)
    )
    # Sort criteria in the order we display them.
    criterion_order = [
        MatchPool.EXACT, MatchPool.DIFF, MatchPool.RESULT,
        MatchPool.PENALTY_WINNER, MatchPool.PENALTY_SCORE, MatchPool.PENALTY_DIFF,
    ]
    pools_by_criterion = {p.criterion: p for p in pools}

    pools_view = []
    for c in criterion_order:
        p = pools_by_criterion.get(c)
        if p is None:
            continue
        # Penalty pools only render once the match has actually gone to penalties.
        if c in _PENALTY_CRITERIA and not (actual is not None and actual.went_to_penalties):
            continue
        pools_view.append({
            "criterion": c,
            "label": _CRITERION_LABEL_TR.get(c, c),
            "pool": p,
            "rows": _format_breakdown(p, slot),
            "winning_key": _winning_breakdown_key(c, slot, actual) if actual else None,
        })

    # Per-user payouts (only when actual result exists).
    user_payouts = []
    if actual is not None:
        scores = (
            GanyanScore.objects
            .filter(slot=slot)
            .exclude(outcome=GanyanScore.NO_RESULT)
            .select_related("user", "effective_round")
            .order_by("-total", "user__nickname")
        )
        for s in scores:
            label, badge_cls = _OUTCOME_BADGE.get(s.outcome, ("", ""))
            user_payouts.append({
                "score": s,
                "user": s.user,
                "label": label,
                "badge_cls": badge_cls,
            })

    return render(request, "scoring/match_detail.html", {
        "tournament": slot.tournament,
        "slot": slot,
        "actual": actual,
        "is_locked": slot.is_locked or actual is not None,
        "pools": pools_view,
        "user_payouts": user_payouts,
    })


def _winning_breakdown_key(criterion: str, slot: BracketSlot, actual: ActualResult) -> str:
    """Which breakdown key is the 'winner' for this criterion + result."""
    if criterion == MatchPool.EXACT:
        return f"{actual.home_score}-{actual.away_score}"
    if criterion == MatchPool.DIFF:
        return str(actual.home_score - actual.away_score)
    if criterion == MatchPool.RESULT:
        if actual.home_score > actual.away_score:
            return "H"
        if actual.home_score < actual.away_score:
            return "A"
        return "D"
    if criterion == MatchPool.PENALTY_WINNER:
        return actual.penalty_winner.code if actual.penalty_winner_id else ""
    if criterion == MatchPool.PENALTY_SCORE:
        if actual.home_penalties is None or actual.away_penalties is None:
            return ""
        return f"{actual.home_penalties}-{actual.away_penalties}"
    if criterion == MatchPool.PENALTY_DIFF:
        if actual.home_penalties is None or actual.away_penalties is None:
            return ""
        return str(actual.home_penalties - actual.away_penalties)
    return ""
