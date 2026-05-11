"""Project-level views (only the home page lives here)."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.predictions.models import SlotPrediction
from apps.scoring.leaderboard import leaderboard_for_tournament
from apps.scoring.models import SlotScore
from apps.tournament.models import ActualResult, BracketSlot, Tournament

# How many items each home-page module shows when there's enough data.
UPCOMING_LIMIT = 4
RESULTS_LIMIT = 4
LEADERBOARD_LIMIT = 12

# Ordering for prediction chips beneath each match: best matchup first.
_MATCHUP_PRIORITY = {
    SlotScore.EXACT: 0,
    SlotScore.DIFF: 1,
    SlotScore.RESULT: 2,
    SlotScore.PENALTY_LOSER_BONUS: 3,
    SlotScore.MISS: 4,
    SlotScore.NO_RESULT: 5,
}


def _chips_for_slots(slot_ids: list[int]) -> dict[int, list[dict]]:
    """For each slot in `slot_ids`, return the list of "prediction chips"
    — one per user who has a SlotScore on that slot.

    Each chip carries the prediction's score, the user's nickname, and the
    matchup_type used to colour-code it (None for pre-result matches).
    When a user has multiple predictions across rounds, the displayed one
    is the engine's "earning" prediction if any was correct, otherwise their
    latest prediction.

    Two queries total (one for scores, one for predictions) regardless of
    how many slots/users are involved — the loops are pure Python.
    """
    if not slot_ids:
        return {}

    scores = list(
        SlotScore.objects
        .filter(slot_id__in=slot_ids)
        .exclude(matchup_type=SlotScore.NO_PREDICTION)
        .select_related("user")
    )

    preds = list(
        SlotPrediction.objects
        .filter(slot_id__in=slot_ids)
        .select_related("home_team", "away_team", "prediction_round")
        .order_by("slot_id", "user_id", "-prediction_round__order")
    )
    # Index: (slot, user) → ordered preds (latest first).
    preds_by_user: dict[tuple[int, int], list[SlotPrediction]] = {}
    for p in preds:
        preds_by_user.setdefault((p.slot_id, p.user_id), []).append(p)

    chips: dict[int, list[dict]] = {}
    for ss in scores:
        bucket = preds_by_user.get((ss.slot_id, ss.user_id), [])
        if not bucket:
            continue
        # Earning round wins; otherwise fall back to the latest prediction.
        display = bucket[0]
        if ss.earning_round_order is not None:
            for p in bucket:
                if p.prediction_round.order == ss.earning_round_order:
                    display = p
                    break
        match_type = ss.matchup_type if ss.matchup_type != SlotScore.NO_RESULT else None
        chips.setdefault(ss.slot_id, []).append({
            "user_id": ss.user_id,
            "nickname": ss.user.nickname or ss.user.email,
            "home_score": display.home_score,
            "away_score": display.away_score,
            "matchup_type": match_type,
            "_sort": (
                _MATCHUP_PRIORITY.get(ss.matchup_type, 99),
                (ss.user.nickname or ss.user.email).lower(),
            ),
        })

    for items in chips.values():
        items.sort(key=lambda c: c["_sort"])
        for c in items:
            del c["_sort"]
    return chips


def home(request: HttpRequest) -> HttpResponse:
    """Overview dashboard. Same modules for everyone — anonymous visitors
    just don't get the personal greeting and the per-row "your prediction"
    / "points you earned" lines.
    """
    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "home.html", {"tournament": None})

    now = timezone.now()
    viewer = request.user if request.user.is_authenticated else None

    # --- Upcoming matches (only slots with both teams known) ---
    upcoming_slots = list(
        BracketSlot.objects
        .filter(
            tournament=tournament,
            scheduled_kickoff__gt=now,
            home_team_actual__isnull=False,
            away_team_actual__isnull=False,
            result__isnull=True,
        )
        .select_related("stage", "home_team_actual", "away_team_actual")
        .order_by("scheduled_kickoff")[:UPCOMING_LIMIT]
    )
    # Latest prediction (across rounds) per upcoming slot — only when there's
    # a logged-in viewer to attach predictions to.
    preds_by_slot: dict[int, SlotPrediction] = {}
    if viewer is not None:
        upcoming_slot_ids = [s.id for s in upcoming_slots]
        for p in (
            SlotPrediction.objects
            .filter(user=viewer, slot_id__in=upcoming_slot_ids)
            .select_related("home_team", "away_team", "prediction_round")
            .order_by("slot_id", "-prediction_round__order")
        ):
            preds_by_slot.setdefault(p.slot_id, p)
    upcoming_chip_map = _chips_for_slots([s.id for s in upcoming_slots])
    upcoming = [
        {
            "slot": s,
            "prediction": preds_by_slot.get(s.id),
            "chips": upcoming_chip_map.get(s.id, []),
        }
        for s in upcoming_slots
    ]

    # --- Recent results ---
    recent_actuals = list(
        ActualResult.objects
        .filter(slot__tournament=tournament)
        .select_related(
            "slot__stage", "slot__home_team_actual", "slot__away_team_actual",
            "penalty_winner",
        )
        .order_by("-slot__scheduled_kickoff")[:RESULTS_LIMIT]
    )
    viewer_scores: dict[int, SlotScore] = {}
    if viewer is not None:
        recent_slot_ids = [a.slot_id for a in recent_actuals]
        viewer_scores = {
            s.slot_id: s
            for s in SlotScore.objects.filter(user=viewer, slot_id__in=recent_slot_ids)
        }
    recent_chip_map = _chips_for_slots([a.slot_id for a in recent_actuals])
    recent = [
        {
            "actual": a,
            "slot": a.slot,
            "viewer_score": viewer_scores.get(a.slot_id),
            "chips": recent_chip_map.get(a.slot_id, []),
        }
        for a in recent_actuals
    ]

    # --- Leaderboard top N ---
    entries = leaderboard_for_tournament(tournament)
    top = entries[:LEADERBOARD_LIMIT]

    return render(request, "home.html", {
        "tournament": tournament,
        "upcoming": upcoming,
        "recent": recent,
        "leaderboard_top": top,
    })
