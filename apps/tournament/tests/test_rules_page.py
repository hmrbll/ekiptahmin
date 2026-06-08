"""Rules page renders stage points and prediction rounds from the active
tournament, so admin tweaks to weights/points are reflected without
template edits."""

from datetime import date, datetime, timezone as dt_tz
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.tournament.models import PredictionRound, Stage, Tournament


@pytest.fixture
def seeded_tournament(db):
    t = Tournament.objects.create(
        name="WC", slug="wc",
        start_date=date(2026, 6, 1), end_date=date(2026, 7, 1),
        is_active=True,
    )
    group = Stage.objects.create(
        tournament=t, kind=Stage.GROUP, order=0,
        points_exact=6, points_diff=4, points_result=2,
        penalty_loser_pct=Decimal("0.60"),
    )
    final = Stage.objects.create(
        tournament=t, kind=Stage.FINAL, order=6,
        points_exact=20, points_diff=14, points_result=7,
        penalty_loser_pct=Decimal("0.60"),
    )
    pre = PredictionRound.objects.create(
        tournament=t, name="Pre-turnuva", order=0,
        weight=Decimal("1.00"),
        deadline=datetime(2026, 6, 11, 19, 0, tzinfo=dt_tz.utc),
    )
    pre.editable_stages.set([group, final])
    return t


def test_rules_page_renders_for_active_tournament(client, seeded_tournament):
    resp = client.get(reverse("rules"))
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    # Heading + at least one section
    assert "Kurallar ve Puanlama" in body
    # Round name comes from DB
    assert "Pre-turnuva" in body
    # Stage ganyan pool figures come from DB (uniform 100 per regulation criterion).
    assert ">100<" in body
    # Stage label is translated to TR
    assert "Grup aşaması" in body
    # Round weight is rendered (Django TR locale uses comma)
    assert "×1,00" in body or "×1.00" in body


def test_rules_page_renders_without_active_tournament(client, db):
    """No active tournament → page still loads with empty tables (defensive
    behaviour for the time between deploy and first seed)."""
    resp = client.get(reverse("rules"))
    assert resp.status_code == 200
    assert "Kurallar ve Puanlama" in resp.content.decode("utf-8")
