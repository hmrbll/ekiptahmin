"""Leaderboard views — read-only aggregations over SlotScore."""

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.predictions.models import SlotPrediction
from apps.tournament.models import (
    ActualResult,
    BracketSlot,
    PredictionRound,
    Tournament,
)

from .leaderboard import describe_ties, leaderboard_for_tournament
from .models import SlotScore


@login_required
def leaderboard(request: HttpRequest) -> HttpResponse:
    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "scoring/no_tournament.html", status=200)

    rounds = list(PredictionRound.objects.filter(tournament=tournament).order_by("order"))
    entries = leaderboard_for_tournament(tournament)

    # Align per-round values with `rounds` so the template can iterate
    # row-by-column without a dict lookup filter.
    rows = []
    for e in entries:
        rows.append({
            "rank": e.rank,
            "user_id": e.user.id,
            "nickname": e.nickname,
            "total": e.total,
            "per_round_values": [e.per_round.get(r.order) for r in rounds],
        })

    return render(request, "scoring/leaderboard.html", {
        "tournament": tournament,
        "rounds": rounds,
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


@login_required
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

    sections_by_kind: dict[str, list[dict]] = {}
    total_points = 0
    for score in scores:
        slot = score.slot
        actual = actuals_by_slot.get(slot.id)
        prediction = _earning_or_latest_prediction(target, slot, score.earning_round_order)
        sections_by_kind.setdefault(slot.stage.kind, []).append({
            "slot": slot,
            "actual": actual,
            "prediction": prediction,
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
