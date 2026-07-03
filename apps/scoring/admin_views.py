"""Staff result-entry wizard — mirrors the prediction wizard structure but
writes ActualResult (and, on knockout slots, BracketSlot team assignments).

Auto-save: each slot row submits over HTMX on input change and the server
returns the freshly rendered row. The scoring signal layer recomputes
SlotScore rows for every affected user as soon as ActualResult lands.

Access: `@staff_member_required` — only Django staff can enter results.
"""

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.scoring.cache import recompute_slot_for_all_users
from apps.tournament.models import ActualResult, BracketSlot, Tournament

from .admin_forms import ActualResultForm, SlotTeamsForm

GROUP_LETTERS = list("ABCDEFGHIJKL")
KNOCKOUT_STAGE_ORDER = ["R32", "R16", "QF", "SF", "THIRD", "FINAL"]
# English on purpose: this wizard lives under /admin/ (admin = EN, site = TR).
KNOCKOUT_LABELS = {
    "R32": "Round of 32", "R16": "Round of 16", "QF": "Quarter Finals",
    "SF": "Semi Finals", "THIRD": "Third Place", "FINAL": "Final",
}


# ---------- Wizard navigation ----------


def _steps() -> list[dict]:
    """The full step list: 12 groups + summary + 6 knockout stages + summary."""
    steps: list[dict] = []
    for letter in GROUP_LETTERS:
        steps.append({
            "key": f"group-{letter}",
            "label": f"Group {letter}",
            "url": reverse("admin_results_group", args=[letter]),
        })
    steps.append({
        "key": "groups-summary",
        "label": "Groups Summary",
        "url": reverse("admin_results_groups_summary"),
    })
    for kind in KNOCKOUT_STAGE_ORDER:
        steps.append({
            "key": f"knockout-{kind}",
            "label": KNOCKOUT_LABELS[kind],
            "url": reverse("admin_results_knockout", args=[kind]),
        })
    steps.append({
        "key": "knockout-summary",
        "label": "Knockout Summary",
        "url": reverse("admin_results_knockout_summary"),
    })
    return steps


def _wrap_steps(current_key: str) -> dict:
    steps = _steps()
    idx = next((i for i, s in enumerate(steps) if s["key"] == current_key), 0)
    return {
        "steps": steps,
        "current_step_idx": idx,
        "prev_step": steps[idx - 1] if idx > 0 else None,
        "next_step": steps[idx + 1] if idx + 1 < len(steps) else None,
    }


# ---------- Row context ----------


def _teams_resolved(slot: BracketSlot) -> bool:
    return bool(slot.home_team_actual_id and slot.away_team_actual_id)


def _parse_score(value) -> int | None:
    """Saved int or raw POST string → int, else None."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _knockout_sections(slot: BracketSlot, home, away, aet_home, aet_away) -> dict:
    """Which auto-revealed sections a knockout row needs.

    A 90' draw reveals the extra-time (120') score inputs; a 120' draw on top
    of that reveals the shootout fields. Values may be saved ints or raw POST
    strings. Group rows never show either.
    """
    if slot.stage.kind == "GROUP":
        return {"show_aet": False, "show_pens": False}
    h, a = _parse_score(home), _parse_score(away)
    show_aet = h is not None and a is not None and h == a
    ah, aa = _parse_score(aet_home), _parse_score(aet_away)
    show_pens = show_aet and ah is not None and aa is not None and ah == aa
    return {"show_aet": show_aet, "show_pens": show_pens}


def _build_row_context(slot: BracketSlot) -> dict:
    """Build the context dict for one slot row in the wizard."""
    actual = ActualResult.objects.filter(slot=slot).first()
    result_form = ActualResultForm(instance=actual, slot=slot)
    # The team picker only appears for a knockout slot the bracket resolver
    # hasn't filled yet. Once teams are known (groups → R32 → R16 → …) the row
    # shows them as fixed labels with flags — no dropdown, nothing to pick.
    teams_form = None
    if slot.stage.kind != "GROUP" and not _teams_resolved(slot):
        teams_form = SlotTeamsForm(instance=slot)
    sections = _knockout_sections(
        slot,
        actual.home_score if actual else None,
        actual.away_score if actual else None,
        actual.home_score_aet if actual else None,
        actual.away_score_aet if actual else None,
    )
    # Legacy safety net: a stored row can claim ET/penalties without the score
    # shape backing it (pre-aet rows) — still surface its sections for repair.
    if actual is not None:
        sections["show_aet"] = sections["show_aet"] or actual.went_to_extra_time
        sections["show_pens"] = sections["show_pens"] or actual.went_to_penalties
    return {
        "slot": slot,
        "actual": actual,
        "result_form": result_form,
        "teams_form": teams_form,
        **sections,
    }


def _slots_for_group(tournament: Tournament, letter: str) -> list[BracketSlot]:
    return list(
        BracketSlot.objects
        .filter(tournament=tournament, stage__kind="GROUP",
                position__startswith=f"Group{letter}-")
        .select_related("stage", "home_team_actual", "away_team_actual")
        .order_by("scheduled_kickoff")
    )


def _slots_for_knockout(tournament: Tournament, kind: str) -> list[BracketSlot]:
    return list(
        BracketSlot.objects
        .filter(tournament=tournament, stage__kind=kind)
        .select_related("stage", "home_team_actual", "away_team_actual")
        .order_by("scheduled_kickoff")
    )


# ---------- Views ----------


def _active_tournament(request):
    return Tournament.objects.filter(is_active=True).first()


@staff_member_required
def admin_results_entry(request: HttpRequest) -> HttpResponse:
    """Redirect to the first wizard step."""
    return redirect("admin_results_group", letter="A")


@staff_member_required
def admin_results_group_step(request: HttpRequest, letter: str) -> HttpResponse:
    tournament = _active_tournament(request)
    if tournament is None:
        return render(request, "admin_results/no_tournament.html", status=200)

    letter = letter.upper()
    if letter not in GROUP_LETTERS:
        return redirect("admin_results_entry")

    slots = _slots_for_group(tournament, letter)
    items = [_build_row_context(s) for s in slots]

    ctx = {
        "tournament": tournament,
        "letter": letter,
        "items": items,
    }
    ctx.update(_wrap_steps(f"group-{letter}"))
    return render(request, "admin_results/group_step.html", ctx)


@staff_member_required
def admin_results_groups_summary(request: HttpRequest) -> HttpResponse:
    tournament = _active_tournament(request)
    if tournament is None:
        return render(request, "admin_results/no_tournament.html", status=200)

    groups = []
    for letter in GROUP_LETTERS:
        slots = _slots_for_group(tournament, letter)
        if not slots:
            continue
        groups.append({
            "letter": letter,
            "items": [_build_row_context(s) for s in slots],
        })

    ctx = {"tournament": tournament, "groups": groups}
    ctx.update(_wrap_steps("groups-summary"))
    return render(request, "admin_results/groups_summary.html", ctx)


@staff_member_required
def admin_results_knockout_step(request: HttpRequest, kind: str) -> HttpResponse:
    tournament = _active_tournament(request)
    if tournament is None:
        return render(request, "admin_results/no_tournament.html", status=200)

    kind = kind.upper()
    if kind not in KNOCKOUT_STAGE_ORDER:
        return redirect("admin_results_entry")

    slots = _slots_for_knockout(tournament, kind)
    items = [_build_row_context(s) for s in slots]

    ctx = {
        "tournament": tournament,
        "stage_label": KNOCKOUT_LABELS[kind],
        "stage_kind": kind,
        "items": items,
    }
    ctx.update(_wrap_steps(f"knockout-{kind}"))
    return render(request, "admin_results/knockout_stage_step.html", ctx)


@staff_member_required
def admin_results_knockout_summary(request: HttpRequest) -> HttpResponse:
    tournament = _active_tournament(request)
    if tournament is None:
        return render(request, "admin_results/no_tournament.html", status=200)

    sections = []
    for kind in KNOCKOUT_STAGE_ORDER:
        slots = _slots_for_knockout(tournament, kind)
        if not slots:
            continue
        sections.append({
            "kind": kind,
            "label": KNOCKOUT_LABELS[kind],
            "items": [_build_row_context(s) for s in slots],
        })

    ctx = {"tournament": tournament, "sections": sections}
    ctx.update(_wrap_steps("knockout-summary"))
    return render(request, "admin_results/knockout_summary.html", ctx)


# ---------- HTMX save endpoint ----------


@staff_member_required
@require_POST
def admin_results_save(request: HttpRequest, slot_id: int) -> HttpResponse:
    slot = get_object_or_404(BracketSlot, pk=slot_id)
    actual = ActualResult.objects.filter(slot=slot).first()

    # Team assignment is only writable for a knockout slot the resolver hasn't
    # filled yet — resolved slots have no picker, so there's nothing to submit.
    teams_form = None
    teams_changed = False
    if slot.stage.kind != "GROUP" and not _teams_resolved(slot):
        teams_form = SlotTeamsForm(request.POST, instance=slot)
        if teams_form.has_changed():
            if teams_form.is_valid():
                slot = teams_form.save()
                teams_changed = True

    result_form = ActualResultForm(request.POST, instance=actual, slot=slot)
    saved = False
    if result_form.is_valid():
        instance: ActualResult = result_form.save(commit=False)
        instance.slot = slot
        instance.entered_by = request.user
        # The wizard is the final word on a result: stamp MANUAL (also when
        # editing an API row) so the live poller never overwrites it again.
        instance.source = ActualResult.SOURCE_MANUAL
        instance.save()
        saved = True
    elif teams_changed:
        # Teams were saved but result form is invalid — that's fine; admin will
        # complete the result in a follow-up edit.
        pass

    if saved or teams_changed:
        # Recompute SlotScore for everyone who has predictions on this slot.
        recompute_slot_for_all_users(slot)

    if request.headers.get("HX-Request"):
        ctx = _build_row_context(slot)
        ctx["just_saved"] = saved or teams_changed
        if not result_form.is_valid():
            ctx["result_form"] = result_form
        # Reveal the ET / penalty sections from the just-submitted scores, so a
        # level score surfaces the next inputs even when the (still-incomplete)
        # save was rejected for missing them.
        ctx.update(_knockout_sections(
            slot,
            request.POST.get("home_score"), request.POST.get("away_score"),
            request.POST.get("home_score_aet"), request.POST.get("away_score_aet"),
        ))
        body = render_to_string("admin_results/_slot_row.html", ctx, request=request)
        return HttpResponse(body)

    return redirect(request.META.get("HTTP_REFERER") or reverse("admin_results_entry"))
