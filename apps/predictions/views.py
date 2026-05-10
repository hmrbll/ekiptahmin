"""End-user prediction views.

Three pages:
- prediction_rounds: list rounds for the active tournament
- prediction_round_detail: list editable slots in a round + each slot's status
- slot_prediction_edit: GET shows form (with carry-over from previous round),
  POST upserts the prediction
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.tournament.models import BracketSlot, PredictionRound, Tournament

from .forms import SlotPredictionForm
from .models import SlotPrediction


@login_required
def prediction_rounds(request: HttpRequest) -> HttpResponse:
    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "predictions/no_tournament.html", status=200)
    rounds = list(tournament.prediction_rounds.all().order_by("order"))
    return render(
        request,
        "predictions/round_list.html",
        {"tournament": tournament, "rounds": rounds},
    )


@login_required
def prediction_round_detail(request: HttpRequest, round_id: int) -> HttpResponse:
    pr = get_object_or_404(
        PredictionRound.objects.select_related("tournament"), pk=round_id,
    )
    stages = list(pr.editable_stages.all())
    slots = (
        BracketSlot.objects
        .filter(tournament=pr.tournament, stage__in=stages)
        .select_related("stage", "home_team_actual", "away_team_actual")
        .order_by("scheduled_kickoff")
    )
    # Cascaded slots may inherit teams from the user's predictions in OTHER
    # rounds — also fetch those so the round detail can show the derived
    # teams. The dict is keyed by slot_id with the user's most recent
    # prediction across all rounds.
    all_user_preds_qs = (
        SlotPrediction.objects
        .filter(user=request.user)
        .select_related("home_team", "away_team", "penalty_winner")
        .order_by("slot_id", "-prediction_round__order")
    )
    latest_per_slot: dict[int, SlotPrediction] = {}
    for p in all_user_preds_qs:
        latest_per_slot.setdefault(p.slot_id, p)

    user_preds_this_round = {
        p.slot_id: p
        for p in all_user_preds_qs
        if p.prediction_round_id == pr.id
    }

    grouped: dict = {}
    for slot in slots:
        pred = user_preds_this_round.get(slot.id)
        # Compute display teams: actual > cascaded-derived > None (source label)
        display_home = slot.home_team_actual
        display_away = slot.away_team_actual
        if display_home is None and slot.home_source_slot_id:
            src = latest_per_slot.get(slot.home_source_slot_id)
            if src:
                display_home = (
                    src.winner_team()
                    if slot.home_source_kind == "WINNER"
                    else src.loser_team()
                )
        if display_away is None and slot.away_source_slot_id:
            src = latest_per_slot.get(slot.away_source_slot_id)
            if src:
                display_away = (
                    src.winner_team()
                    if slot.away_source_kind == "WINNER"
                    else src.loser_team()
                )
        grouped.setdefault(slot.stage, []).append({
            "slot": slot,
            "pred": pred,
            "display_home": display_home,
            "display_away": display_away,
        })
    stage_groups = [(stage, items) for stage, items in grouped.items()]
    return render(
        request,
        "predictions/round_detail.html",
        {"round": pr, "stage_groups": stage_groups},
    )


@login_required
def slot_prediction_edit(
    request: HttpRequest, round_id: int, slot_id: int,
) -> HttpResponse:
    pr = get_object_or_404(PredictionRound, pk=round_id)
    slot = get_object_or_404(BracketSlot, pk=slot_id, tournament=pr.tournament)

    instance = SlotPrediction.objects.filter(
        user=request.user, prediction_round=pr, slot=slot,
    ).first()

    initial: dict = {}
    if instance is None:
        # Carry-over from the latest earlier round, if any.
        prev = (
            SlotPrediction.objects
            .filter(
                user=request.user, slot=slot,
                prediction_round__order__lt=pr.order,
            )
            .order_by("-prediction_round__order")
            .first()
        )
        if prev:
            initial = {
                "home_team": prev.home_team, "away_team": prev.away_team,
                "home_score": prev.home_score, "away_score": prev.away_score,
                "penalty_winner": prev.penalty_winner,
                "home_penalties": prev.home_penalties,
                "away_penalties": prev.away_penalties,
            }

    if request.method == "POST":
        form = SlotPredictionForm(
            request.POST, instance=instance,
            user=request.user, prediction_round=pr, slot=slot,
        )
        if form.is_valid():
            form.save()
            return redirect("prediction_round_detail", round_id=pr.id)
    else:
        form = SlotPredictionForm(
            instance=instance, initial=initial,
            user=request.user, prediction_round=pr, slot=slot,
        )

    return render(
        request,
        "predictions/slot_edit.html",
        {
            "form": form, "round": pr, "slot": slot, "instance": instance,
            "cascade_blocked_on": form.cascade_blocked_on,
        },
    )
