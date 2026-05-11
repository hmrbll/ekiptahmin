"""Leaderboard views — read-only aggregations over SlotScore."""

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.tournament.models import PredictionRound, Tournament

from .leaderboard import describe_ties, leaderboard_for_tournament


@login_required
def leaderboard(request: HttpRequest) -> HttpResponse:
    tournament = Tournament.objects.filter(is_active=True).first()
    if tournament is None:
        return render(request, "scoring/no_tournament.html", status=200)

    rounds = list(PredictionRound.objects.filter(tournament=tournament).order_by("order"))
    entries = leaderboard_for_tournament(tournament)

    # Align per-round values with `rounds` so the template can iterate
    # row-by-column without a dict lookup filter.
    rows = []
    for e in entries:
        rows.append({
            "rank": e.rank,
            "nickname": e.nickname,
            "total": e.total,
            "per_round_values": [e.per_round.get(r.order) for r in rounds],
        })

    return render(request, "scoring/leaderboard.html", {
        "tournament": tournament,
        "rounds": rounds,
        "rows": rows,
        "tie_notes": describe_ties(entries),
    })
