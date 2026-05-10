"""End-user prediction views.

- prediction_rounds: list rounds for the active tournament
- prediction_round_detail: single page with inline forms for every slot in
  the round's editable stages — the user's whole prediction surface lives here
- slot_prediction_save: POST-only endpoint that processes one slot's form;
  returns a row fragment for HTMX requests, redirects back to the round detail
  for non-HTMX submits
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

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


def _build_row_context(request, pr, slot, all_user_latest, this_round_pred):
    """Build the per-slot context dict consumed by `_slot_row.html`.

    Carries the form, the user's current prediction (if any), and the
    display teams (which may differ from the form's choice — e.g., when
    the cascade is blocked but the slot is still rendered).
    """
    instance = this_round_pred
    initial: dict = {}
    if instance is None:
        # Carry-over from the latest earlier round, if any.
        prev = (
            SlotPrediction.objects
            .filter(
                user=request.user, slot=slot,
                prediction_round__order__lt=pr.order,
            )
            .select_related("home_team", "away_team", "penalty_winner")
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

    form = SlotPredictionForm(
        instance=instance, initial=initial,
        user=request.user, prediction_round=pr, slot=slot,
    )

    display_home = slot.home_team_actual
    display_away = slot.away_team_actual
    # If the cascade resolved a team for the form, surface it for read-only display
    # too — this is what makes "A Grubu 2.si" turn into the actual team name.
    if display_home is None:
        if form.fields["home_team"].initial:
            display_home = form.fields["home_team"].initial
    if display_away is None:
        if form.fields["away_team"].initial:
            display_away = form.fields["away_team"].initial
    # Fallback: derive from slot-cascade source's prediction (also covers
    # rendering rows that the user is not actively editing).
    if display_home is None and slot.home_source_slot_id:
        src = all_user_latest.get(slot.home_source_slot_id)
        if src:
            display_home = (
                src.winner_team()
                if slot.home_source_kind == "WINNER" else src.loser_team()
            )
    if display_away is None and slot.away_source_slot_id:
        src = all_user_latest.get(slot.away_source_slot_id)
        if src:
            display_away = (
                src.winner_team()
                if slot.away_source_kind == "WINNER" else src.loser_team()
            )

    return {
        "slot": slot,
        "pred": instance,
        "form": form,
        "display_home": display_home,
        "display_away": display_away,
        "cascade_blocked_on": form.cascade_blocked_on,
        "round": pr,
    }


@login_required
def prediction_round_detail(request: HttpRequest, round_id: int) -> HttpResponse:
    pr = get_object_or_404(
        PredictionRound.objects.select_related("tournament"), pk=round_id,
    )
    stages = list(pr.editable_stages.all().order_by("order"))
    slots = list(
        BracketSlot.objects
        .filter(tournament=pr.tournament, stage__in=stages)
        .select_related("stage", "home_team_actual", "away_team_actual",
                        "home_source_slot", "away_source_slot")
        .order_by("scheduled_kickoff")
    )

    all_user_preds_qs = list(
        SlotPrediction.objects
        .filter(user=request.user)
        .select_related("home_team", "away_team", "penalty_winner")
        .order_by("slot_id", "-prediction_round__order")
    )
    all_user_latest: dict[int, SlotPrediction] = {}
    for p in all_user_preds_qs:
        all_user_latest.setdefault(p.slot_id, p)
    this_round_preds: dict[int, SlotPrediction] = {
        p.slot_id: p for p in all_user_preds_qs if p.prediction_round_id == pr.id
    }

    grouped: dict = {}
    for slot in slots:
        ctx = _build_row_context(
            request, pr, slot,
            all_user_latest, this_round_preds.get(slot.id),
        )
        grouped.setdefault(slot.stage, []).append(ctx)
    stage_groups = [(stage, items) for stage, items in grouped.items()]
    return render(
        request,
        "predictions/round_detail.html",
        {"round": pr, "stage_groups": stage_groups},
    )


@login_required
@require_POST
def slot_prediction_save(
    request: HttpRequest, round_id: int, slot_id: int,
) -> HttpResponse:
    pr = get_object_or_404(PredictionRound, pk=round_id)
    slot = get_object_or_404(BracketSlot, pk=slot_id, tournament=pr.tournament)

    instance = SlotPrediction.objects.filter(
        user=request.user, prediction_round=pr, slot=slot,
    ).first()

    form = SlotPredictionForm(
        request.POST, instance=instance,
        user=request.user, prediction_round=pr, slot=slot,
    )
    saved = form.is_valid()
    if saved:
        form.save()

    if request.headers.get("HX-Request"):
        # Re-fetch to render the row context with the saved state.
        all_user_latest = {
            p.slot_id: p
            for p in SlotPrediction.objects
                .filter(user=request.user)
                .select_related("home_team", "away_team", "penalty_winner")
                .order_by("slot_id", "-prediction_round__order")
        }
        this_round_pred = (
            SlotPrediction.objects
            .filter(user=request.user, prediction_round=pr, slot=slot)
            .select_related("home_team", "away_team", "penalty_winner")
            .first()
        )
        ctx = _build_row_context(request, pr, slot, all_user_latest, this_round_pred)
        ctx["just_saved"] = saved
        if not saved:
            # The fresh form (built inside _build_row_context) lost the
            # submitted bound state — keep the bound form so errors render.
            ctx["form"] = form
        return render(request, "predictions/_slot_row.html", ctx)

    return redirect("prediction_round_detail", round_id=pr.id)
