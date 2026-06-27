"""Project-level views (home + rules)."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.liveresults.sync import live_syncs
from apps.predictions.models import SlotPrediction
from apps.scoring.ganyan_leaderboard import leaderboard_for_tournament
from apps.scoring.models import GanyanScore
from apps.tournament.models import ActualResult, BracketSlot, PredictionRound, Stage, Tournament

# How many items each home-page module shows when there's enough data.
UPCOMING_LIMIT = 4
RESULTS_LIMIT = 4
LEADERBOARD_LIMIT = 12

def _score_spectrum_key(home: int, away: int) -> tuple[int, int]:
    """Sort key arranging predicted scores from "strong home win" through
    draws to "strong away win". Per Hemre's spec:

    - Primary: -(home - away)  — bigger home margin first, then draws, then
      bigger away margins.
    - Tiebreaker within home wins / draws: bigger home score (= bigger gf)
      first.
    - Tiebreaker within away wins: smaller home score first, so the smallest
      away win (0-1) precedes a bigger one (1-2). This grows toward the
      "max away" end of the spectrum.

    Example sort: 4-1, 3-0, 2-2, 1-1, 0-1, 1-2.
    """
    diff = home - away
    primary = -diff
    secondary = -home if diff >= 0 else home
    return (primary, secondary)


def _chips_for_slots(slot_ids: list[int]) -> dict[int, list[dict]]:
    """For each slot in `slot_ids`, return the list of "prediction chips" —
    one per user whose prediction is visible.

    Visibility rule: a slot's predictions become public once its RESULT is
    entered (not merely at kickoff). A slot without an ActualResult yields no
    chips, so the set stays empty until the match is scored.

    Each chip carries the prediction's score, the user's nickname, and a
    `matchup_type` (the user's GanyanScore.outcome) for colour coding. Since a
    chip only appears once the result is in, the colour always reflects a real
    outcome tier.
    """
    if not slot_ids:
        return {}

    # A slot's predictions are revealed only once its result is entered.
    visible_slot_ids = list(
        ActualResult.objects
        .filter(slot_id__in=slot_ids)
        .values_list("slot_id", flat=True)
    )
    if not visible_slot_ids:
        return {}

    preds = list(
        SlotPrediction.objects
        .filter(slot_id__in=visible_slot_ids)
        .select_related("user", "home_team", "away_team", "prediction_round")
        .order_by("slot_id", "user_id", "-prediction_round__order")
    )
    # Index: (slot, user) → ordered preds (latest first).
    preds_by_user: dict[tuple[int, int], list[SlotPrediction]] = {}
    for p in preds:
        preds_by_user.setdefault((p.slot_id, p.user_id), []).append(p)

    # GanyanScore rows for the visible (result-entered) slots. Keyed
    # (slot_id, user_id) to match the per-prediction `key` used below.
    ganyan_by_user_slot: dict[tuple[int, int], GanyanScore] = {
        (gs.slot_id, gs.user_id): gs
        for gs in GanyanScore.objects.filter(slot_id__in=visible_slot_ids)
    }

    chips: dict[int, list[dict]] = {}
    seen_user_slots: set[tuple[int, int]] = set()
    for p in preds:
        key = (p.slot_id, p.user_id)
        if key in seen_user_slots:
            continue
        seen_user_slots.add(key)
        # Picked prediction: the user's effective_round if scored, else latest.
        gs = ganyan_by_user_slot.get(key)
        if gs and gs.effective_round_id is not None:
            display = next(
                (pp for pp in preds_by_user[key] if pp.prediction_round_id == gs.effective_round_id),
                p,
            )
        else:
            display = p  # latest (ordered desc)
        match_type = gs.outcome if gs and gs.outcome not in (GanyanScore.NO_RESULT, GanyanScore.NO_PREDICTION) else None
        user = display.user
        nick = user.nickname or user.email
        chips.setdefault(p.slot_id, []).append({
            "user_id": user.id,
            "nickname": nick,
            "home_score": display.home_score,
            "away_score": display.away_score,
            "matchup_type": match_type,
            # Round the shown pick came from — drives the weight badge. The
            # pre-tournament round (order 0, ×1.00) is the baseline, so the
            # template omits its badge.
            "round_order": display.prediction_round.order,
            "round_weight": display.prediction_round.weight,
            "_sort": (
                *_score_spectrum_key(display.home_score, display.away_score),
                nick.lower(),
            ),
        })

    for items in chips.values():
        items.sort(key=lambda c: c["_sort"])
        for c in items:
            del c["_sort"]
    return chips


def _grid_context(request: HttpRequest, tournament) -> dict:
    """Build the three dashboard columns (upcoming / recent / leaderboard).

    Shared by `home` (initial full-page render) and `home_grid` (the HTMX
    partial polled every 30s so a finished match lands in 'Son sonuçlar' and
    the leaderboard moves without a manual refresh).
    """
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

    # --- Recent results (excluding matches currently in play — those show in
    # the live module above, not here, so a live score isn't duplicated).
    # Uses the same `live_syncs` definition as the live module (cap included), so
    # a match stuck IN_PLAY past its cap is no longer "live" and resurfaces here
    # instead of vanishing from both. ---
    live_slot_ids = {ms.slot_id for ms in live_syncs(tournament, now=now)}
    recent_actuals = list(
        ActualResult.objects
        .filter(slot__tournament=tournament)
        .exclude(slot_id__in=live_slot_ids)
        .select_related(
            "slot__stage", "slot__home_team_actual", "slot__away_team_actual",
            "penalty_winner",
        )
        .order_by("-slot__scheduled_kickoff")[:RESULTS_LIMIT]
    )
    viewer_scores: dict[int, GanyanScore] = {}
    if viewer is not None:
        recent_slot_ids = [a.slot_id for a in recent_actuals]
        viewer_scores = {
            s.slot_id: s
            for s in GanyanScore.objects.filter(user=viewer, slot_id__in=recent_slot_ids)
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

    return {"upcoming": upcoming, "recent": recent, "leaderboard_top": top}


def home(request: HttpRequest) -> HttpResponse:
    """Overview dashboard. Same modules for everyone — anonymous visitors
    just don't get the personal greeting and the per-row "your prediction"
    / "points you earned" lines.
    """
    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "home.html", {"tournament": None})
    ctx = {"tournament": tournament, **_grid_context(request, tournament)}
    return render(request, "home.html", ctx)


def home_grid(request: HttpRequest) -> HttpResponse:
    """HTMX partial: the three dashboard columns, polled every 30s so results
    and the leaderboard stay live without a full-page refresh."""
    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "_home_grid.html", {"tournament": None})
    ctx = {"tournament": tournament, **_grid_context(request, tournament)}
    return render(request, "_home_grid.html", ctx)


def rules(request: HttpRequest) -> HttpResponse:
    """Static-content page explaining the prediction format, rounds, scoring,
    penalties, and tiebreakers. Data is pulled from the active tournament so
    admin edits to stage points or round weights are reflected automatically.
    """
    tournament = Tournament.objects.filter(is_active=True).first()
    stages_view: list[dict] = []
    rounds_view: list[dict] = []
    if tournament is not None:
        stages = list(Stage.objects.filter(tournament=tournament).order_by("order"))
        stages_view = [
            {
                "name_tr": s.kind_label_tr,
                "pool_exact": s.pool_exact,
                "pool_diff": s.pool_diff,
                "pool_result": s.pool_result,
                "pool_penalty_winner": s.pool_penalty_winner,
                "pool_penalty_score": s.pool_penalty_score,
                "pool_penalty_diff": s.pool_penalty_diff,
                "kind": s.kind,
            }
            for s in stages
        ]
        rounds_qs = (
            PredictionRound.objects
            .filter(tournament=tournament)
            .prefetch_related("editable_stages")
            .select_related("depends_on_stage")
            .order_by("order")
        )
        rounds_view = [
            {
                "order": r.order,
                "name": r.name,
                "deadline": r.deadline,
                "weight": r.weight,
                "depends_on_tr": (
                    r.depends_on_stage.kind_label_tr if r.depends_on_stage else None
                ),
                "editable_tr": [s.kind_label_tr for s in r.editable_stages.all()],
            }
            for r in rounds_qs
        ]
    return render(request, "rules.html", {
        "tournament": tournament,
        "stages": stages_view,
        "rounds": rounds_view,
    })
