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


def home(request: HttpRequest) -> HttpResponse:
    """Marketing landing for guests; overview dashboard for authenticated users."""
    if not request.user.is_authenticated:
        return render(request, "home.html", {})

    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "home.html", {"tournament": None})

    now = timezone.now()

    # --- Upcoming matches (only slots with both teams known) ---
    upcoming_slots = list(
        BracketSlot.objects
        .filter(
            tournament=tournament,
            scheduled_kickoff__gt=now,
            home_team_actual__isnull=False,
            away_team_actual__isnull=False,
        )
        .select_related("stage", "home_team_actual", "away_team_actual")
        .order_by("scheduled_kickoff")[:UPCOMING_LIMIT]
    )
    # Latest prediction (across rounds) per upcoming slot for this user.
    upcoming_slot_ids = [s.id for s in upcoming_slots]
    preds_by_slot: dict[int, SlotPrediction] = {}
    for p in (
        SlotPrediction.objects
        .filter(user=request.user, slot_id__in=upcoming_slot_ids)
        .select_related("home_team", "away_team", "prediction_round")
        .order_by("slot_id", "-prediction_round__order")
    ):
        preds_by_slot.setdefault(p.slot_id, p)
    upcoming = [{"slot": s, "prediction": preds_by_slot.get(s.id)} for s in upcoming_slots]

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
    # Look up the viewer's earned points for each recent result.
    recent_slot_ids = [a.slot_id for a in recent_actuals]
    viewer_scores = {
        s.slot_id: s
        for s in SlotScore.objects.filter(user=request.user, slot_id__in=recent_slot_ids)
    }
    recent = [
        {"actual": a, "slot": a.slot, "viewer_score": viewer_scores.get(a.slot_id)}
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
