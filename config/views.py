"""Project-level views (home + rules)."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.predictions.models import SlotPrediction
from apps.scoring.ganyan_leaderboard import leaderboard_for_tournament
from apps.scoring.models import GanyanScore, MatchPool
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

    Visibility rule: post-lock predictions are public. Pre-lock predictions
    are hidden (slot.is_locked drives the chip set per slot).

    Each chip carries the prediction's score, the user's nickname, and a
    `matchup_type` for colour coding. After result entry the colour reflects
    the user's GanyanScore.outcome; before result it's None (neutral chip).
    """
    if not slot_ids:
        return {}

    slots_by_id = {
        s.id: s for s in BracketSlot.objects.filter(id__in=slot_ids).only(
            "id", "scheduled_kickoff",
        )
    }
    locked_slot_ids = [sid for sid, s in slots_by_id.items() if s.is_locked]
    if not locked_slot_ids:
        return {}

    preds = list(
        SlotPrediction.objects
        .filter(slot_id__in=locked_slot_ids)
        .select_related("user", "home_team", "away_team", "prediction_round")
        .order_by("slot_id", "user_id", "-prediction_round__order")
    )
    # Index: (slot, user) → ordered preds (latest first).
    preds_by_user: dict[tuple[int, int], list[SlotPrediction]] = {}
    for p in preds:
        preds_by_user.setdefault((p.slot_id, p.user_id), []).append(p)

    # GanyanScore rows for the locked slots — only exist when a result is in.
    ganyan_by_user_slot: dict[tuple[int, int], GanyanScore] = {
        (gs.user_id, gs.slot_id): gs
        for gs in GanyanScore.objects.filter(slot_id__in=locked_slot_ids)
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

    return render(request, "home.html", {
        "tournament": tournament,
        "upcoming": upcoming,
        "recent": recent,
        "leaderboard_top": top,
    })


_STAGE_TR = {
    Stage.GROUP: "Grup aşaması",
    Stage.R32: "Son 32",
    Stage.R16: "Son 16",
    Stage.QF: "Çeyrek final",
    Stage.SF: "Yarı final",
    Stage.THIRD: "Üçüncülük maçı",
    Stage.FINAL: "Final",
}


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
                "name_tr": _STAGE_TR.get(s.kind, s.get_kind_display()),
                "pool_exact": s.pool_exact,
                "pool_diff": s.pool_diff,
                "pool_result": s.pool_result,
                "pool_penalty_pass": s.pool_penalty_pass,
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
                    _STAGE_TR.get(r.depends_on_stage.kind, r.depends_on_stage.get_kind_display())
                    if r.depends_on_stage else None
                ),
                "editable_tr": [
                    _STAGE_TR.get(s.kind, s.get_kind_display())
                    for s in r.editable_stages.all()
                ],
            }
            for r in rounds_qs
        ]
    return render(request, "rules.html", {
        "tournament": tournament,
        "stages": stages_view,
        "rounds": rounds_view,
    })
