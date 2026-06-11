"""LEGACY bracket-scoring views (SlotScore engine).

Staff-only post-cutover (mounted under /legacy/ from `legacy_urls.py`).
Public site uses ganyan engine — see `views.py`.

`user_detail` redacts pre-lock predictions for visitors who aren't the
row's owner so the cascade game isn't ruined by peeking — preserved here
even though access is staff-only, in case Hemre views as someone else.
"""

from django.contrib.auth import get_user_model
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.predictions.models import SlotPrediction
from apps.tournament.models import (
    ActualResult,
    Tournament,
)

from .leaderboard import describe_ties, leaderboard_for_tournament
from .models import SlotScore


def leaderboard(request: HttpRequest) -> HttpResponse:
    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "scoring/no_tournament.html", status=200)

    entries = leaderboard_for_tournament(tournament)

    # Per-matchup-type counts (Hemre's column spec). `wrong` is `miss` only;
    # `no_prediction` gets its own column so the two failure modes (predicted
    # wrong vs. didn't predict at all) stay distinguishable.
    rows = []
    for e in entries:
        c = e.counts
        rows.append({
            "rank": e.rank,
            "user_id": e.user.id,
            "nickname": e.nickname,
            "total": e.total,
            "exact": c.get(SlotScore.EXACT, 0),
            "diff": c.get(SlotScore.DIFF, 0),
            "result": c.get(SlotScore.RESULT, 0),
            "penalty_bonus": c.get(SlotScore.PENALTY_LOSER_BONUS, 0),
            "wrong": c.get(SlotScore.MISS, 0),
            "no_prediction": c.get(SlotScore.NO_PREDICTION, 0),
        })

    return render(request, "scoring/leaderboard.html", {
        "tournament": tournament,
        "rows": rows,
        "tie_notes": describe_ties(entries),
    })


# Stages, in tournament progression order. Used to group the user detail page.
_STAGE_ORDER = ["GROUP", "R32", "R16", "QF", "SF", "THIRD", "FINAL"]


def _earning_or_latest_prediction(user, slot, earning_round_order):
    """Find the SlotPrediction whose score is reflected in the SlotScore row.

    If `earning_round_order` is set, that's the one. Otherwise (miss /
    no-result / no-prediction) we surface the user's latest prediction so
    the detail page shows what they were predicting — even if it didn't
    earn points.
    """
    qs = SlotPrediction.objects.filter(user=user, slot=slot).select_related(
        "home_team", "away_team", "penalty_winner", "prediction_round",
    )
    if earning_round_order is not None:
        return qs.filter(prediction_round__order=earning_round_order).first()
    return qs.order_by("-prediction_round__order").first()


def user_detail(request: HttpRequest, user_id: int) -> HttpResponse:
    """Per-slot score sheet for one user."""
    User = get_user_model()
    target = get_object_or_404(User, pk=user_id)
    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "scoring/no_tournament.html", status=200)

    scores = (
        SlotScore.objects
        .filter(user=target, slot__tournament=tournament)
        .select_related("slot__stage", "slot__home_team_actual", "slot__away_team_actual")
        .order_by("slot__stage__order", "slot__scheduled_kickoff")
    )

    # Fetch actuals + earning predictions in bulk to avoid N+1.
    slot_ids = [s.slot_id for s in scores]
    actuals_by_slot = {
        a.slot_id: a for a in ActualResult.objects.filter(slot_id__in=slot_ids).select_related(
            "slot__home_team_actual", "slot__away_team_actual", "penalty_winner",
        )
    }

    # Lock rule: a target's pre-lock prediction is hidden from everyone but
    # the owner. Once kickoff passes or an actual result is entered, the
    # prediction becomes public.
    is_self = request.user.is_authenticated and request.user.id == target.id

    sections_by_kind: dict[str, list[dict]] = {}
    total_points = 0
    for score in scores:
        slot = score.slot
        actual = actuals_by_slot.get(slot.id)
        is_locked = slot.is_locked or actual is not None
        raw_prediction = _earning_or_latest_prediction(target, slot, score.earning_round_order)
        # Hide pre-lock predictions from non-owners — but remember whether
        # one existed so the template can render "kilit sonrası görünür"
        # instead of the regular "tahmin yok" fallback.
        prediction_visible = is_self or is_locked
        sections_by_kind.setdefault(slot.stage.kind, []).append({
            "slot": slot,
            "actual": actual,
            "prediction": raw_prediction if prediction_visible else None,
            "prediction_hidden": (not prediction_visible) and raw_prediction is not None,
            "score": score,
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


# Matchup-type → (Turkish label, Tailwind badge classes) for the results page.
# Same tier colour convention as _OUTCOME_BADGE in views.py.
_MATCHUP_BADGE = {
    SlotScore.EXACT: ("Tam skor", "bg-primary/10 border-primary/30 text-primary"),
    SlotScore.DIFF: ("Aynı fark", "bg-success/10 border-success/30 text-success"),
    SlotScore.RESULT: ("Doğru sonuç", "bg-warning/10 border-warning/30 text-warning"),
    SlotScore.PENALTY_LOSER_BONUS: ("Penaltı bonusu", "bg-accent/10 border-accent/30 text-accent"),
    SlotScore.MISS: ("Yanlış", "bg-danger/10 border-danger/30 text-danger"),
    SlotScore.NO_PREDICTION: ("Tahmin yok", "bg-surface border-line text-fg-muted"),
}


def results_list(request: HttpRequest) -> HttpResponse:
    """Played matches (those with an ActualResult) ordered by kickoff,
    each with the per-user point breakdown.
    """
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
        .order_by("-slot__scheduled_kickoff")
    )

    slot_ids = [a.slot_id for a in actuals]
    score_rows = (
        SlotScore.objects
        .filter(slot_id__in=slot_ids)
        # `no_result` rows shouldn't exist when ActualResult is present, but
        # filter defensively so a stale row doesn't sneak in.
        .exclude(matchup_type=SlotScore.NO_RESULT)
        .select_related("user")
        .order_by("-total", "user__nickname")
    )

    # Pre-fetch the earning (or latest) prediction for every (user, slot) row
    # we're about to render so the template can show what each user predicted.
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

    def _pick_prediction(user_id, slot_id, earning_order):
        bucket = preds_by_user_slot.get((user_id, slot_id), [])
        if not bucket:
            return None
        if earning_order is not None:
            for p in bucket:
                if p.prediction_round.order == earning_order:
                    return p
        return bucket[0]  # latest (queryset is desc-ordered)

    scores_by_slot: dict[int, list] = {}
    for s in score_rows:
        label, badge_cls = _MATCHUP_BADGE.get(s.matchup_type, ("", ""))
        scores_by_slot.setdefault(s.slot_id, []).append({
            "score": s,
            "user": s.user,
            "label": label,
            "badge_cls": badge_cls,
            "prediction": _pick_prediction(s.user_id, s.slot_id, s.earning_round_order),
        })

    matches = []
    for actual in actuals:
        matches.append({
            "actual": actual,
            "slot": actual.slot,
            "scores": scores_by_slot.get(actual.slot_id, []),
        })

    return render(request, "scoring/results.html", {
        "tournament": tournament,
        "matches": matches,
    })


def scoring_diff(request: HttpRequest) -> HttpResponse:
    """Side-by-side SlotScore vs GanyanScore leaderboard for calibration.

    Renders both engines' totals per user so Hemre can sanity-check the
    new system against the bracket model. Staff-only — mounted under
    /legacy/scoring-diff/.
    """
    from decimal import Decimal

    from .ganyan_leaderboard import leaderboard_for_tournament as ganyan_lb

    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "scoring/no_tournament.html", status=200)

    legacy_entries = leaderboard_for_tournament(tournament)
    ganyan_entries = ganyan_lb(tournament)

    legacy_by_user = {e.user.id: e for e in legacy_entries}
    ganyan_by_user = {e.user.id: e for e in ganyan_entries}

    all_user_ids = set(legacy_by_user) | set(ganyan_by_user)
    rows = []
    for uid in all_user_ids:
        leg = legacy_by_user.get(uid)
        gan = ganyan_by_user.get(uid)
        user = (leg.user if leg else gan.user)
        leg_total = leg.total if leg else Decimal("0")
        gan_total = gan.total if gan else Decimal("0")
        rows.append({
            "user": user,
            "nickname": (gan.nickname if gan else leg.nickname),
            "legacy_total": leg_total,
            "legacy_rank": leg.rank if leg else None,
            "ganyan_total": gan_total,
            "ganyan_rank": gan.rank if gan else None,
            "delta": gan_total - leg_total,
        })
    rows.sort(key=lambda r: (-r["ganyan_total"], -r["legacy_total"]))

    return render(request, "scoring/legacy_scoring_diff.html", {
        "tournament": tournament,
        "rows": rows,
    })
