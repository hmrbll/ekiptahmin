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

from .cascade import downstream_slots, invalidate_stale_predictions
from .forms import SlotPredictionForm
from .models import BracketCompletionEvent, SlotPrediction
from .standings import standings_for_group

GROUP_LETTERS = list("ABCDEFGHIJKL")

# Knockout stage ordering + Turkish labels for the wizard step pills.
KNOCKOUT_STAGE_ORDER = ["R32", "R16", "QF", "SF", "THIRD", "FINAL"]
KNOCKOUT_LABELS = {
    "R32": "Son 32",
    "R16": "Son 16",
    "QF": "Çeyrek Final",
    "SF": "Yarı Final",
    "THIRD": "3.lük",
    "FINAL": "Final",
}


# ---------- Index views ----------


# ---------- Public "all predictions" view ----------


# Slot is treated as locked (predictions become public) when EITHER its
# kickoff has passed OR an ActualResult has been entered. The latter covers
# matches that admin tested ahead of schedule.
def _slot_predictions_public(slot: BracketSlot) -> bool:
    return slot.is_locked or hasattr(slot, "result")


def predictions_all(request: HttpRequest) -> HttpResponse:
    """Match-by-match public view of every user's predictions.

    No login required. Predictions are revealed only after a slot has
    locked (kickoff passed or actual result entered) — pre-lock predictions
    stay private so users don't copy each other.
    """
    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "predictions/all.html", {"tournament": None})

    slots = list(
        BracketSlot.objects
        .filter(tournament=tournament)
        .select_related("stage", "home_team_actual", "away_team_actual", "result")
        .order_by("scheduled_kickoff")
    )

    # Fetch the latest prediction per (user, slot) across rounds in one pass.
    slot_ids = [s.id for s in slots]
    preds_qs = (
        SlotPrediction.objects
        .filter(slot_id__in=slot_ids)
        .select_related("user", "home_team", "away_team", "prediction_round")
        .order_by("slot_id", "user_id", "-prediction_round__order")
    )
    latest_by_slot_user: dict[tuple[int, int], SlotPrediction] = {}
    for p in preds_qs:
        latest_by_slot_user.setdefault((p.slot_id, p.user_id), p)

    # Count, per slot, how many distinct users predicted (used for the
    # pre-lock "N kişi tahmin etti" hint).
    prediction_counts: dict[int, int] = {}
    for slot_id, _user_id in latest_by_slot_user.keys():
        prediction_counts[slot_id] = prediction_counts.get(slot_id, 0) + 1

    matches = []
    for slot in slots:
        # Skip slots that don't have teams determined yet — there's nothing
        # meaningful to show for them on a per-match page.
        if not (slot.home_team_actual_id and slot.away_team_actual_id):
            continue

        is_public = _slot_predictions_public(slot)
        slot_preds = []
        if is_public:
            slot_preds = sorted(
                (p for (sid, _uid), p in latest_by_slot_user.items() if sid == slot.id),
                key=lambda p: (p.user.nickname or p.user.email or "").lower(),
            )
        matches.append({
            "slot": slot,
            "actual": getattr(slot, "result", None),
            "is_public": is_public,
            "predictions": slot_preds,
            "prediction_count": prediction_counts.get(slot.id, 0),
        })

    return render(request, "predictions/all.html", {
        "tournament": tournament,
        "matches": matches,
    })


# ---------- "My predictions" round list ----------


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
            "label": "Grup Özet",
            "url": reverse("predict_groups_summary", args=[pr.id]),
        })

    editable_knockout_kinds = set(
        pr.editable_stages.exclude(kind="GROUP").values_list("kind", flat=True)
    )
    knockout_kinds_in_order = [k for k in KNOCKOUT_STAGE_ORDER if k in editable_knockout_kinds]
    for kind in knockout_kinds_in_order:
        steps.append({
            "key": f"knockout-{kind}",
            "label": KNOCKOUT_LABELS[kind],
            "url": reverse("predict_knockout_stage_step", args=[pr.id, kind]),
        })
    if knockout_kinds_in_order:
        steps.append({
            "key": "knockout-summary",
            "label": "Eleme Özet",
            "url": reverse("predict_knockout_summary", args=[pr.id]),
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

    # Carry-over prefill only makes sense while the matchup is the same match.
    # If the derived teams (written into form.initial by the form itself)
    # differ from the previous round's teams, drop the stale scoreline so the
    # slot renders as never predicted.
    if instance is None and initial and (
        form.initial.get("home_team") != initial.get("home_team")
        or form.initial.get("away_team") != initial.get("away_team")
    ):
        for key in ("home_score", "away_score",
                    "penalty_winner", "home_penalties", "away_penalties"):
            form.initial.pop(key, None)

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


def _knockout_section_for_stage(request, pr, stage_kind):
    """Returns a (stage, items) tuple for one knockout stage in this tournament."""
    try:
        stage = pr.tournament.stages.get(kind=stage_kind)
    except pr.tournament.stages.model.DoesNotExist:
        return None
    slots = list(
        BracketSlot.objects
        .filter(tournament=pr.tournament, stage=stage)
        .select_related("stage", "home_team_actual", "away_team_actual",
                        "home_source_slot", "away_source_slot")
        .order_by("scheduled_kickoff")
    )
    if not slots:
        return None
    all_latest, this_round = _user_preds_index(request.user, pr)
    items = [
        _build_row_context(request, pr, slot, all_latest, this_round.get(slot.id))
        for slot in slots
    ]
    return (stage, items)


@login_required
def predict_knockout_stage_step(
    request: HttpRequest, round_id: int, kind: str,
) -> HttpResponse:
    """One knockout stage in isolation (Son 32, Son 16, Çeyrek Final, ...)."""
    pr = get_object_or_404(PredictionRound.objects.select_related("tournament"), pk=round_id)
    kind = kind.upper()
    if kind not in KNOCKOUT_STAGE_ORDER:
        return redirect("predict_round_entry", round_id=pr.id)
    if not pr.editable_stages.filter(kind=kind).exists():
        return redirect("predict_round_entry", round_id=pr.id)

    section = _knockout_section_for_stage(request, pr, kind)
    if section is None:
        return redirect("predict_round_entry", round_id=pr.id)

    ctx = {
        "round": pr,
        "stage": section[0],
        "items": section[1],
        "stage_label": KNOCKOUT_LABELS[kind],
    }
    ctx.update(_wrap_steps(pr, f"knockout-{kind}"))
    return render(request, "predictions/knockout_stage_step.html", ctx)


@login_required
def predict_knockout_summary(request: HttpRequest, round_id: int) -> HttpResponse:
    """All editable knockout stages on one page (review + edit)."""
    pr = get_object_or_404(PredictionRound.objects.select_related("tournament"), pk=round_id)
    if not _has_knockout_step(pr):
        return redirect("predict_round_entry", round_id=pr.id)

    editable_knockout_kinds = set(
        pr.editable_stages.exclude(kind="GROUP").values_list("kind", flat=True)
    )
    sections = []
    for kind in KNOCKOUT_STAGE_ORDER:
        if kind not in editable_knockout_kinds:
            continue
        section = _knockout_section_for_stage(request, pr, kind)
        if section is not None:
            sections.append(section)

    ctx = {"round": pr, "sections": sections}
    ctx.update(_wrap_steps(pr, "knockout-summary"))
    return render(request, "predictions/knockout_summary.html", ctx)


def _check_and_mark_bracket_complete(user, pr) -> bool:
    """If this is the first time `user` has a SlotPrediction for every
    editable slot in `pr`, create a BracketCompletionEvent marker and
    return True. Subsequent calls (or partial brackets) return False —
    so the caller fires the GA4 `bracket_tamamlandi` event at most once
    per (user, round).
    """
    editable_slot_count = BracketSlot.objects.filter(
        tournament_id=pr.tournament_id,
        stage__in=pr.editable_stages.all(),
    ).count()
    if editable_slot_count == 0:
        return False
    user_pred_count = (
        SlotPrediction.objects
        .filter(user=user, slot__tournament_id=pr.tournament_id,
                slot__stage__in=pr.editable_stages.all())
        .values("slot_id").distinct().count()
    )
    if user_pred_count < editable_slot_count:
        return False
    _, created = BracketCompletionEvent.objects.get_or_create(
        user=user, prediction_round=pr,
    )
    return created


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
    invalidated: list[BracketSlot] = []
    if saved:
        form.save()
        # The edit may have changed downstream matchups (R32 winner feeds
        # R16, group standings feed R32, ...). Predictions whose matchup
        # went stale are deleted — they must look never-predicted.
        invalidated = invalidate_stale_predictions(request.user, pr)

    if request.headers.get("HX-Request"):
        all_latest, this_round = _user_preds_index(request.user, pr)
        ctx = _build_row_context(request, pr, slot, all_latest, this_round.get(slot.id))
        ctx["just_saved"] = saved
        if not saved:
            ctx["form"] = form
        if saved:
            ctx["bracket_just_completed"] = _check_and_mark_bracket_complete(request.user, pr)

        body = render_to_string("predictions/_slot_row.html", ctx, request=request)

        # Refresh dependent rows in place (knockout summary shows several
        # stages at once): every invalidated slot, plus — for knockout saves —
        # all transitively fed slots, whose displayed teams may have changed
        # even without a deletion. Sent as hx-swap-oob fragments; htmx drops
        # the ones not present on the current page.
        if saved:
            editable_stage_ids = set(pr.editable_stages.values_list("id", flat=True))
            refresh = {s.id: s for s in invalidated}
            if slot.stage.kind != "GROUP":
                for ds in downstream_slots(slot):
                    refresh.setdefault(ds.id, ds)
            for ds in refresh.values():
                if ds.id == slot.id or ds.stage_id not in editable_stage_ids:
                    continue
                ds_ctx = _build_row_context(request, pr, ds, all_latest, this_round.get(ds.id))
                ds_ctx["oob"] = True
                body += render_to_string("predictions/_slot_row.html", ds_ctx, request=request)

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
