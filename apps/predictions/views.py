"""End-user prediction views.

Round detail is a wizard:
- For rounds whose editable_stages include GROUP: walk through Group A → ...
  → Group L → "Özet" → "Eleme Turları"
- For rounds without GROUP: jump straight to "Eleme Turları"

Each group page has inline forms with auto-save (HTMX) and a live standings
table. The summary collects all groups on one page (still editable).
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.tournament.models import BracketSlot, PredictionRound, Tournament

from .forms import SlotPredictionForm
from .models import SlotPrediction
from .standings import standings_for_group

GROUP_LETTERS = list("ABCDEFGHIJKL")


# ---------- Index views ----------


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


# ---------- Helpers ----------


def _user_preds_index(user, pr):
    """Return (all_user_latest_per_slot, this_round_preds_by_slot)."""
    qs = list(
        SlotPrediction.objects
        .filter(user=user)
        .select_related("home_team", "away_team", "penalty_winner")
        .order_by("slot_id", "-prediction_round__order")
    )
    all_latest: dict[int, SlotPrediction] = {}
    for p in qs:
        all_latest.setdefault(p.slot_id, p)
    this_round = {p.slot_id: p for p in qs if p.prediction_round_id == pr.id}
    return all_latest, this_round


def _has_group_step(pr) -> bool:
    return pr.editable_stages.filter(kind="GROUP").exists()


def _has_knockout_step(pr) -> bool:
    return pr.editable_stages.exclude(kind="GROUP").exists()


def _round_steps(pr) -> list[dict]:
    """Ordered list of wizard steps for this round."""
    steps: list[dict] = []
    if _has_group_step(pr):
        for letter in GROUP_LETTERS:
            steps.append({
                "key": f"group-{letter}",
                "label": f"Grup {letter}",
                "url": reverse("predict_group_step", args=[pr.id, letter]),
            })
        steps.append({
            "key": "groups-summary",
            "label": "Özet",
            "url": reverse("predict_groups_summary", args=[pr.id]),
        })
    if _has_knockout_step(pr):
        steps.append({
            "key": "knockout",
            "label": "Eleme Turları",
            "url": reverse("predict_knockout_step", args=[pr.id]),
        })
    return steps


def _wrap_steps(pr, current_key: str) -> dict:
    """Build the {steps, prev_step, next_step, current_step_idx} payload."""
    steps = _round_steps(pr)
    idx = next((i for i, s in enumerate(steps) if s["key"] == current_key), 0)
    return {
        "steps": steps,
        "current_step_idx": idx,
        "prev_step": steps[idx - 1] if idx > 0 else None,
        "next_step": steps[idx + 1] if idx + 1 < len(steps) else None,
    }


def _build_row_context(request, pr, slot, all_user_latest, this_round_pred):
    """Per-slot context dict consumed by `_slot_row.html`."""
    instance = this_round_pred
    initial: dict = {}
    if instance is None:
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
    if display_home is None and form.fields["home_team"].initial:
        display_home = form.fields["home_team"].initial
    if display_away is None and form.fields["away_team"].initial:
        display_away = form.fields["away_team"].initial
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


def _build_group_block(request, pr, letter, all_user_latest, this_round_preds):
    """Build the group card payload (slot rows + standings)."""
    group_slots = list(
        BracketSlot.objects
        .filter(
            tournament=pr.tournament, stage__kind="GROUP",
            position__startswith=f"Group{letter}-",
        )
        .select_related("stage", "home_team_actual", "away_team_actual",
                        "home_source_slot", "away_source_slot")
        .order_by("scheduled_kickoff")
    )
    items = [
        _build_row_context(request, pr, slot, all_user_latest, this_round_preds.get(slot.id))
        for slot in group_slots
    ]
    standings = standings_for_group(request.user, pr.tournament, letter)
    teams_by_code = {t.code: t for t in pr.tournament.teams.all()}
    standings_rows = [{"team": teams_by_code.get(s.team_code), "stat": s} for s in standings]
    return {"letter": letter, "items": items, "standings": standings_rows}


# ---------- Wizard entry ----------


@login_required
def predict_round_entry(request: HttpRequest, round_id: int) -> HttpResponse:
    """Redirect to the first wizard step for this round."""
    pr = get_object_or_404(PredictionRound, pk=round_id)
    steps = _round_steps(pr)
    if not steps:
        return render(request, "predictions/no_steps.html", {"round": pr})
    return redirect(steps[0]["url"])


# ---------- Group step ----------


@login_required
def predict_group_step(
    request: HttpRequest, round_id: int, letter: str,
) -> HttpResponse:
    pr = get_object_or_404(PredictionRound.objects.select_related("tournament"), pk=round_id)
    letter = letter.upper()
    if not _has_group_step(pr) or letter not in GROUP_LETTERS:
        return redirect("predict_round_entry", round_id=pr.id)

    all_latest, this_round = _user_preds_index(request.user, pr)
    group = _build_group_block(request, pr, letter, all_latest, this_round)
    if not group["items"]:
        return redirect("predict_round_entry", round_id=pr.id)

    ctx = {"round": pr, "group": group}
    ctx.update(_wrap_steps(pr, f"group-{letter}"))
    return render(request, "predictions/group_step.html", ctx)


# ---------- Groups summary ----------


@login_required
def predict_groups_summary(request: HttpRequest, round_id: int) -> HttpResponse:
    pr = get_object_or_404(PredictionRound.objects.select_related("tournament"), pk=round_id)
    if not _has_group_step(pr):
        return redirect("predict_round_entry", round_id=pr.id)

    all_latest, this_round = _user_preds_index(request.user, pr)
    groups = [
        _build_group_block(request, pr, letter, all_latest, this_round)
        for letter in GROUP_LETTERS
        if _build_group_block_has_slots(pr, letter)
    ]

    ctx = {"round": pr, "groups": groups}
    ctx.update(_wrap_steps(pr, "groups-summary"))
    return render(request, "predictions/groups_summary.html", ctx)


def _build_group_block_has_slots(pr, letter: str) -> bool:
    return BracketSlot.objects.filter(
        tournament=pr.tournament, stage__kind="GROUP",
        position__startswith=f"Group{letter}-",
    ).exists()


# ---------- Knockout step ----------


@login_required
def predict_knockout_step(request: HttpRequest, round_id: int) -> HttpResponse:
    pr = get_object_or_404(PredictionRound.objects.select_related("tournament"), pk=round_id)
    if not _has_knockout_step(pr):
        return redirect("predict_round_entry", round_id=pr.id)

    knockout_stages = list(
        pr.editable_stages.exclude(kind="GROUP").order_by("order")
    )
    slots = list(
        BracketSlot.objects
        .filter(tournament=pr.tournament, stage__in=knockout_stages)
        .select_related("stage", "home_team_actual", "away_team_actual",
                        "home_source_slot", "away_source_slot")
        .order_by("scheduled_kickoff")
    )

    all_latest, this_round = _user_preds_index(request.user, pr)
    grouped: dict = {}
    for slot in slots:
        item = _build_row_context(request, pr, slot, all_latest, this_round.get(slot.id))
        grouped.setdefault(slot.stage, []).append(item)

    sections = [(stage, items) for stage, items in grouped.items()]

    ctx = {"round": pr, "sections": sections}
    ctx.update(_wrap_steps(pr, "knockout"))
    return render(request, "predictions/knockout_step.html", ctx)


# ---------- HTMX save endpoint ----------


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
        all_latest, _ = _user_preds_index(request.user, pr)
        this_round_pred = (
            SlotPrediction.objects
            .filter(user=request.user, prediction_round=pr, slot=slot)
            .select_related("home_team", "away_team", "penalty_winner")
            .first()
        )
        ctx = _build_row_context(request, pr, slot, all_latest, this_round_pred)
        ctx["just_saved"] = saved
        if not saved:
            ctx["form"] = form

        body = render_to_string("predictions/_slot_row.html", ctx, request=request)

        # Group slot save → also push the updated standings table for that
        # group via HTMX out-of-band swap, so the live ranking refreshes.
        if saved and slot.stage.kind == "GROUP":
            letter = slot.position.split("-")[0].replace("Group", "")
            standings = standings_for_group(request.user, pr.tournament, letter)
            teams_by_code = {t.code: t for t in pr.tournament.teams.all()}
            rows = [{"team": teams_by_code.get(s.team_code), "stat": s} for s in standings]
            standings_html = render_to_string(
                "predictions/_standings_table.html",
                {"letter": letter, "standings": rows, "oob": True},
                request=request,
            )
            body += standings_html

        return HttpResponse(body)

    return redirect("predict_round_entry", round_id=pr.id)


# ---------- Backwards compatibility ----------

# Old name kept so any saved bookmarks / external links still work.
prediction_round_detail = predict_round_entry
