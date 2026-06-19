"""The homepage live-scores partial.

Polled by HTMX every ~30s. Each poll best-effort refreshes the data from
football-data (throttled to one external call / 45s across all visitors via
maybe_sync_live) and then renders whatever matches are currently in play.

The external API is never touched per-visitor — maybe_sync_live throttles and
no-ops outside match windows; this view just reads our DB.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.scoring.models import GanyanScore
from apps.tournament.models import ActualResult, Tournament

from .models import MatchSync
from .sync import live_cap, maybe_sync_live


def live_scores(request: HttpRequest) -> HttpResponse:
    maybe_sync_live()

    tournament = Tournament.objects.filter(is_active=True).first()
    items: list[dict] = []
    if tournament is not None:
        viewer = request.user if request.user.is_authenticated else None
        now = timezone.now()
        syncs = [
            ms for ms in (
                MatchSync.objects
                .filter(slot__tournament=tournament,
                        status__in=MatchSync.LIVE_STATUSES, finalized=False)
                .select_related("slot__stage", "slot__home_team_actual", "slot__away_team_actual")
                .order_by("slot__scheduled_kickoff")
            )
            # Drop matches stuck IN_PLAY past their cap (FINISHED never arrived).
            if now <= ms.slot.scheduled_kickoff + live_cap(ms.slot.stage.kind)
        ]
        slot_ids = [ms.slot_id for ms in syncs]
        actuals = {
            a.slot_id: a for a in ActualResult.objects.filter(slot_id__in=slot_ids)
        }
        scores: dict[int, GanyanScore] = {}
        if viewer is not None and slot_ids:
            scores = {
                s.slot_id: s
                for s in GanyanScore.objects.filter(user=viewer, slot_id__in=slot_ids)
            }
        items = [
            {
                "sync": ms,
                "slot": ms.slot,
                "actual": actuals.get(ms.slot_id),
                "viewer_score": scores.get(ms.slot_id),
            }
            for ms in syncs
        ]

    return render(request, "liveresults/_live_scores.html", {"live": items})
