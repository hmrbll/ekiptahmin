"""Public scoring views (active ganyan engine).

Three routes:
- `/leaderboard/`              — overall ranked board (GanyanScore + new tiebreakers)
- `/leaderboard/<user_id>/`    — per-user score sheet
- `/results/`                  — played-matches log with ganyan payouts
- `/matches/<slot_id>/`        — single-match detail + ganyan tablosu

Legacy bracket views (SlotScore) live under /legacy/ — see `legacy_views.py`.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.liveresults.sync import live_syncs
from apps.predictions.models import SlotPrediction
from apps.tournament.models import (
    ActualResult,
    BracketSlot,
    Tournament,
)
from apps.tournament.sections import group_matches_into_sections

from .ganyan_leaderboard import (
    describe_ties,
    leaderboard_for_tournament,
    leaderboard_sections_for_tournament,
)
from .models import GanyanScore, MatchPool


_STAGE_ORDER = ["GROUP", "R32", "R16", "QF", "SF", "THIRD", "FINAL"]


def _pick_on_actual_fixture(pred, slot) -> bool:
    """Whether a prediction is on the slot's actual fixture (strict home AND away).

    A knockout slot gathers every player's bracket pick, so most picks here are
    for a different matchup than the teams that actually reached this slot. Only
    a pick on the real fixture belongs on a match/result view — it's the only
    pick that can score and the only one the ganyan tablosu counts toward N or
    shows in the breakdown (engine: `ganyan._matchup_correct`). No-op for group
    slots, whose fixture is fixed. `pred` may be None → False.
    """
    return (
        pred is not None
        and pred.home_team_id == slot.home_team_actual_id
        and pred.away_team_id == slot.away_team_actual_id
    )

# Outcome → (TR label, Tailwind badge classes) for chip/badge rendering.
# Tier colours must match the leaderboard pills and the inline badge logic in
# user_detail.html / _prediction_chip.html (exact=primary, diff=success,
# result=warning, penalty=accent, miss=danger).
_OUTCOME_BADGE = {
    GanyanScore.EXACT: ("Tam skor", "bg-primary/10 border-primary/30 text-primary"),
    GanyanScore.DIFF: ("Aynı fark", "bg-success/10 border-success/30 text-success"),
    GanyanScore.RESULT: ("Doğru sonuç", "bg-warning/10 border-warning/30 text-warning"),
    GanyanScore.PENALTY: ("Penaltı", "bg-accent/10 border-accent/30 text-accent"),
    GanyanScore.MISS: ("Yanlış", "bg-danger/10 border-danger/30 text-danger"),
    GanyanScore.NO_PREDICTION: ("Tahmin yok", "bg-surface border-line text-fg-muted"),
}


def _leaderboard_rows(entries) -> list[dict]:
    rows = []
    for e in entries:
        c = e.counts
        # Cumulative counts: an exact score also nails the goal difference
        # and the outcome, so it counts in all three columns (mirrors the
        # weighted tiebreaker semantics in ganyan_leaderboard).
        exact = c.get(GanyanScore.EXACT, 0)
        diff_or_better = exact + c.get(GanyanScore.DIFF, 0)
        result_or_better = diff_or_better + c.get(GanyanScore.RESULT, 0)
        rows.append({
            "rank": e.rank,
            "user_id": e.user.id,
            "nickname": e.nickname,
            "total": e.total,
            "exact": exact,
            "diff": diff_or_better,
            "result": result_or_better,
            "penalty": c.get(GanyanScore.PENALTY, 0),
            "wrong": c.get(GanyanScore.MISS, 0),
            # Points earned per criterion — the "Puan" face of the toggle.
            # Already cumulative by construction: an exact hit wins the
            # exact, diff AND result pools.
            "points_exact": e.score_breakdown["exact"],
            "points_diff": e.score_breakdown["diff"],
            "points_result": e.score_breakdown["result"],
            "points_penalty": e.score_breakdown["penalty"],
        })
    return rows


def leaderboard(request: HttpRequest) -> HttpResponse:
    """Ranked board, tabbed by round: a "Genel" (overall) tab plus one tab per
    scored round section — the same sections the all-predictions and results
    pages tab by. Each round tab re-ranks users on that round's matches only.
    """
    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "scoring/no_tournament.html", status=200)

    overall = leaderboard_for_tournament(tournament)
    tabs = [{
        "key": "overall",
        "label": "Genel",
        "rows": _leaderboard_rows(overall),
        "tie_notes": describe_ties(overall),
    }]
    for section in leaderboard_sections_for_tournament(tournament):
        tabs.append({
            "key": section["key"],
            "label": section["label"],
            "rows": _leaderboard_rows(section["entries"]),
            "tie_notes": describe_ties(section["entries"]),
        })

    return render(request, "scoring/leaderboard.html", {
        "tournament": tournament,
        "tabs": tabs,
        "default_tab_key": "overall",
    })


def _earning_or_latest_prediction(user, slot, effective_round_id):
    """Find the SlotPrediction whose ganyan score is being shown.

    If `effective_round_id` is set, that's the one. Otherwise (miss /
    no-result / no-prediction) we surface the user's latest prediction.
    """
    qs = SlotPrediction.objects.filter(user=user, slot=slot).select_related(
        "home_team", "away_team", "penalty_winner", "prediction_round",
    )
    if effective_round_id is not None:
        result = qs.filter(prediction_round_id=effective_round_id).first()
        if result is not None:
            return result
    return qs.order_by("-prediction_round__order").first()


def user_detail(request: HttpRequest, user_id: int) -> HttpResponse:
    """Per-slot ganyan score sheet for one user."""
    User = get_user_model()
    target = get_object_or_404(User, pk=user_id)
    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "scoring/no_tournament.html", status=200)

    scores = (
        GanyanScore.objects
        .filter(user=target, slot__tournament=tournament)
        .select_related("slot__stage", "slot__home_team_actual", "slot__away_team_actual", "effective_round")
        .order_by("slot__stage__order", "slot__scheduled_kickoff")
    )

    slot_ids = [s.slot_id for s in scores]
    actuals_by_slot = {
        a.slot_id: a for a in ActualResult.objects.filter(slot_id__in=slot_ids).select_related(
            "slot__home_team_actual", "slot__away_team_actual", "penalty_winner",
        )
    }

    is_self = request.user.is_authenticated and request.user.id == target.id

    sections_by_kind: dict[str, list[dict]] = {}
    total_points = Decimal("0")
    for score in scores:
        slot = score.slot
        actual = actuals_by_slot.get(slot.id)
        # Predictions go public once the slot's prediction round has closed (the
        # pick is final) or it's scored — not at the match's own kickoff.
        revealed = actual is not None or slot.predictions_round_closed
        raw_prediction = _earning_or_latest_prediction(target, slot, score.effective_round_id)
        prediction_visible = is_self or revealed
        label, badge_cls = _OUTCOME_BADGE.get(score.outcome, ("", ""))
        sections_by_kind.setdefault(slot.stage.kind, []).append({
            "slot": slot,
            "actual": actual,
            "prediction": raw_prediction if prediction_visible else None,
            "prediction_hidden": (not prediction_visible) and raw_prediction is not None,
            "score": score,
            "outcome_label": label,
            "outcome_badge_cls": badge_cls,
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


def results_list(request: HttpRequest) -> HttpResponse:
    """Played matches log with ganyan payouts and per-user breakdowns."""
    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "scoring/no_tournament.html", status=200)

    actuals = list(
        ActualResult.objects
        .filter(slot__tournament=tournament)
        .select_related(
            "slot__stage", "slot__home_team_actual", "slot__away_team_actual",
            "penalty_winner",
        )
        # Chronological so each round tab reads match 1 → match 2; the round
        # grouping below preserves this order within every section.
        .order_by("slot__scheduled_kickoff")
    )

    slot_ids = [a.slot_id for a in actuals]
    score_rows = (
        GanyanScore.objects
        .filter(slot_id__in=slot_ids)
        .exclude(outcome=GanyanScore.NO_RESULT)
        .select_related("user", "effective_round")
        .order_by("-total", "user__nickname")
    )

    user_slot_pairs = {(s.user_id, s.slot_id): s for s in score_rows}
    all_preds = (
        SlotPrediction.objects
        .filter(slot_id__in=slot_ids, user_id__in={uid for uid, _ in user_slot_pairs})
        .select_related("home_team", "away_team", "prediction_round")
        .order_by("user_id", "slot_id", "-prediction_round__order")
    )
    preds_by_user_slot: dict[tuple[int, int], list] = {}
    for p in all_preds:
        preds_by_user_slot.setdefault((p.user_id, p.slot_id), []).append(p)

    def _pick_prediction(user_id, slot_id, effective_round_id):
        bucket = preds_by_user_slot.get((user_id, slot_id), [])
        if not bucket:
            return None
        if effective_round_id is not None:
            for p in bucket:
                if p.prediction_round_id == effective_round_id:
                    return p
        return bucket[0]

    # Slots whose match is still being played. A live score is written to
    # ActualResult as it goes, so the ganyan standings here are the live ("anlık")
    # puan durumu off the current score; `is_live` only drives the CANLI badge and
    # the "değişebilir" wording. Same source of truth the homepage CANLI module uses.
    live_slot_ids = {ms.slot_id for ms in live_syncs(tournament)}

    slot_by_id = {a.slot_id: a.slot for a in actuals}
    # Distinct predictors per slot (any matchup) — drives the "N predicted but
    # nobody hit the matchup" note when every pick was off-fixture. One score
    # row == one predictor (the engine only scores users who predicted).
    predictor_count_by_slot: dict[int, int] = {}
    for s in score_rows:
        predictor_count_by_slot[s.slot_id] = predictor_count_by_slot.get(s.slot_id, 0) + 1

    scores_by_slot: dict[int, list] = {}
    for s in score_rows:
        prediction = _pick_prediction(s.user_id, s.slot_id, s.effective_round_id)
        # Knockout: drop picks that aren't on the actual fixture — they didn't
        # predict THIS match and can't score it. Keeps the player list equal to
        # the ganyan tablosu's N / breakdown (see `_pick_on_actual_fixture`).
        slot = slot_by_id.get(s.slot_id)
        if slot is None or not _pick_on_actual_fixture(prediction, slot):
            continue
        label, badge_cls = _OUTCOME_BADGE.get(s.outcome, ("", ""))
        scores_by_slot.setdefault(s.slot_id, []).append({
            "score": s,
            "user": s.user,
            "label": label,
            "badge_cls": badge_cls,
            "prediction": prediction,
        })

    matches = []
    for actual in actuals:
        matches.append({
            "actual": actual,
            "slot": actual.slot,
            "is_live": actual.slot_id in live_slot_ids,
            "scores": scores_by_slot.get(actual.slot_id, []),
            "prediction_count": predictor_count_by_slot.get(actual.slot_id, 0),
        })

    sections, default_section_key = group_matches_into_sections(matches)

    return render(request, "scoring/results.html", {
        "tournament": tournament,
        "sections": sections,
        "default_section_key": default_section_key,
    })


# Criterion → TR label for the ganyan tablosu.
_CRITERION_LABEL_TR = {
    MatchPool.EXACT: "Tam skor",
    MatchPool.DIFF: "Doğru fark",
    MatchPool.RESULT: "Doğru sonuç",
    MatchPool.ADVANCER: "Turlayan",
    MatchPool.PENALTY_WINNER: "Penaltı kazananı",
    MatchPool.PENALTY_SCORE: "Penaltı skoru",
    MatchPool.PENALTY_DIFF: "Penaltı farkı",
}

# Shootout criteria only render once a KO match has gone to penalties.
_PENALTY_CRITERIA = (
    MatchPool.ADVANCER, MatchPool.PENALTY_WINNER,
    MatchPool.PENALTY_SCORE, MatchPool.PENALTY_DIFF,
)

# Result direction key → TR label for the "result" criterion breakdown.
_RESULT_KEY_TR = {"H": "Ev sahibi kazanır", "A": "Deplasman kazanır", "D": "Berabere"}


def _format_breakdown(pool: MatchPool, slot: BracketSlot) -> list[dict]:
    """Build the per-prediction-value rows of the ganyan tablosu."""
    rows = []
    pool_size = pool.pool_size
    for key, count in sorted(pool.breakdown.items(), key=lambda kv: (-kv[1], kv[0])):
        # Pre-result rows have winner_count = 0 but breakdown filled in;
        # show "if this is correct → X puan" as the potential payout.
        # Post-result rows have winner_count set; only the winning key pays.
        potential = (Decimal(pool_size) / count) if count else None
        if pool.criterion == MatchPool.RESULT:
            display_key = _RESULT_KEY_TR.get(key, key)
        else:
            display_key = key
        rows.append({
            "key": key,
            "display_key": display_key,
            "count": count,
            "potential_payout": potential,
        })
    return rows


def match_detail(request: HttpRequest, slot_id: int) -> HttpResponse:
    """Single-match detail page with the full ganyan tablosu.

    Visible to anyone but the ganyan tablosu only renders after lock.
    """
    slot = get_object_or_404(
        BracketSlot.objects.select_related(
            "tournament", "stage", "home_team_actual", "away_team_actual",
        ),
        pk=slot_id,
    )
    actual = ActualResult.objects.filter(slot=slot).select_related("penalty_winner").first()
    # A live score is treated as the result: the ganyan tablosu and the standings
    # below are computed from the current running score so the page shows a live
    # ("anlık") puan durumu, badged CANLI. `is_live` only drives that badge and
    # the "değişebilir" wording — not what's shown. Same "currently live" source
    # as the homepage CANLI module.
    is_live = slot.id in {ms.slot_id for ms in live_syncs(slot.tournament)}
    pools = list(
        MatchPool.objects.filter(slot=slot)
    )
    # Sort criteria in the order we display them.
    criterion_order = [
        MatchPool.EXACT, MatchPool.DIFF, MatchPool.RESULT,
        MatchPool.ADVANCER,
        MatchPool.PENALTY_WINNER, MatchPool.PENALTY_SCORE, MatchPool.PENALTY_DIFF,
    ]
    pools_by_criterion = {p.criterion: p for p in pools}

    pools_view = []
    for c in criterion_order:
        p = pools_by_criterion.get(c)
        if p is None:
            continue
        # Penalty pools only render once the match has actually gone to penalties.
        if c in _PENALTY_CRITERIA and not (actual is not None and actual.went_to_penalties):
            continue
        rows = _format_breakdown(p, slot)
        pools_view.append({
            "criterion": c,
            "label": _CRITERION_LABEL_TR.get(c, c),
            "pool": p,
            "rows": rows,
            # Per-criterion prediction count = the breakdown total, NOT the
            # match-level predictor_count. They match for exact/diff/result/
            # advancer (everyone has a value), but the shootout-only pools
            # (penalty winner/score/diff) only count draw-predictors — so a
            # decisive match shows "0 tahmin" here instead of the misleading N.
            "prediction_count": sum(r["count"] for r in rows),
            "winning_key": _winning_breakdown_key(c, slot, actual) if actual else None,
        })

    # Per-user payouts — computed live off the running score while in play.
    user_payouts = []
    if actual is not None:
        scores = list(
            GanyanScore.objects
            .filter(slot=slot)
            .exclude(outcome=GanyanScore.NO_RESULT)
            .select_related("user", "effective_round")
            .order_by("-total", "user__nickname")
        )
        # Each user's effective-round pick, to drop off-fixture knockout picks —
        # exactly the picks the ganyan tablosu above counts (engine:
        # `_matchup_correct`). No-op for group slots. Keyed (user_id, round_id)
        # since a user may have picked this slot in several rounds.
        eff_preds = {
            (p.user_id, p.prediction_round_id): p
            for p in SlotPrediction.objects.filter(
                slot=slot, user_id__in=[s.user_id for s in scores],
            ).only("user_id", "prediction_round_id", "home_team_id", "away_team_id")
        }
        for s in scores:
            pred = eff_preds.get((s.user_id, s.effective_round_id))
            if not _pick_on_actual_fixture(pred, slot):
                continue
            label, badge_cls = _OUTCOME_BADGE.get(s.outcome, ("", ""))
            user_payouts.append({
                "score": s,
                "user": s.user,
                "label": label,
                "badge_cls": badge_cls,
            })

    return render(request, "scoring/match_detail.html", {
        "tournament": slot.tournament,
        "slot": slot,
        "actual": actual,
        "is_live": is_live,
        # Tablosu (incl. the pre-result pool preview) is public once the slot's
        # prediction round has closed (picks can no longer change) or it's scored
        # — not at the match's own kickoff. (The home-grid chips are stricter:
        # result-only, since they need GanyanScore.outcome for colour coding.)
        "revealed": actual is not None or slot.predictions_round_closed,
        "pools": pools_view,
        "user_payouts": user_payouts,
    })


def _winning_breakdown_key(criterion: str, slot: BracketSlot, actual: ActualResult) -> str:
    """Which breakdown key is the 'winner' for this criterion + result."""
    if criterion == MatchPool.EXACT:
        return f"{actual.home_score}-{actual.away_score}"
    if criterion == MatchPool.DIFF:
        return str(actual.home_score - actual.away_score)
    if criterion == MatchPool.RESULT:
        if actual.home_score > actual.away_score:
            return "H"
        if actual.home_score < actual.away_score:
            return "A"
        return "D"
    if criterion in (MatchPool.PENALTY_WINNER, MatchPool.ADVANCER):
        return actual.penalty_winner.code if actual.penalty_winner_id else ""
    if criterion == MatchPool.PENALTY_SCORE:
        if actual.home_penalties is None or actual.away_penalties is None:
            return ""
        return f"{actual.home_penalties}-{actual.away_penalties}"
    if criterion == MatchPool.PENALTY_DIFF:
        if actual.home_penalties is None or actual.away_penalties is None:
            return ""
        return str(actual.home_penalties - actual.away_penalties)
    return ""
