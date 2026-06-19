"""Tests for the daily digest: slate windowing, completeness gating, the
12:00 fallback, dedup, and recipient fan-out.

Score-dependent details (exact ganyan payouts) are covered by the scoring
tests; here we exercise the *scheduling/sending* logic, so we avoid asserting
on computed GanyanScore values to stay robust.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from apps.notifications import digest
from apps.notifications.models import EmailLog
from apps.predictions.models import SlotPrediction
from apps.tournament.models import (
    ActualResult,
    BracketSlot,
    PredictionRound,
    Stage,
    Team,
    Tournament,
)

SLATE = date(2026, 6, 18)


def _ist(y, m, d, hh, mm=0):
    return timezone.make_aware(datetime(y, m, d, hh, mm), timezone.get_current_timezone())


# ---------------------------------------------------------------- windowing


def test_slate_window_runs_1300_to_1300():
    start, end = digest.slate_window(SLATE)
    assert start == _ist(2026, 6, 18, 13)
    assert end == _ist(2026, 6, 19, 13)


def test_morning_slate_date_is_today():
    assert digest.morning_slate_date(_ist(2026, 6, 18, 13)) == date(2026, 6, 18)


def test_evening_slate_date_is_yesterday():
    # Evening poll the next morning reports the previous slate.
    assert digest.evening_slate_date(_ist(2026, 6, 19, 8)) == date(2026, 6, 18)


def test_evening_final_poll_only_at_noon():
    assert digest.is_evening_final_poll(_ist(2026, 6, 19, 11)) is False
    assert digest.is_evening_final_poll(_ist(2026, 6, 19, 12)) is True


# ------------------------------------------------------------------- dedup


@pytest.mark.django_db
def test_digest_already_sent_ignores_failed_only():
    EmailLog.objects.create(email="a@x.com", kind=EmailLog.DAILY_EVENING,
                            subject="s", status=EmailLog.FAILED, slate_date=SLATE)
    assert EmailLog.digest_already_sent(EmailLog.DAILY_EVENING, SLATE) is False
    EmailLog.objects.create(email="a@x.com", kind=EmailLog.DAILY_EVENING,
                            subject="s", status=EmailLog.SENT, slate_date=SLATE)
    assert EmailLog.digest_already_sent(EmailLog.DAILY_EVENING, SLATE) is True


# -------------------------------------------------------------- integration


@pytest.fixture
def slate(db):
    """Two group matches kicking off inside the 2026-06-18 slate, predicted by
    two players. The round deadline is in the past so predictions are public."""
    t = Tournament.objects.create(
        name="Test Cup", slug="test-cup",
        start_date=date(2026, 6, 1), end_date=date(2026, 7, 1), is_active=True,
    )
    sg = Stage.objects.create(
        tournament=t, kind=Stage.GROUP, order=0,
        points_exact=6, points_diff=4, points_result=2, penalty_loser_pct=Decimal("0.60"),
    )
    pr = PredictionRound.objects.create(
        tournament=t, name="Pre", order=0,
        deadline=timezone.now() - timedelta(days=1),  # closed → predictions public
        weight=Decimal("1.00"),
    )
    pr.editable_stages.set([sg])

    tur = Team.objects.create(tournament=t, code="TUR", name_tr="Türkiye", group_letter="A")
    bra = Team.objects.create(tournament=t, code="BRA", name_tr="Brezilya", group_letter="A")
    arg = Team.objects.create(tournament=t, code="ARG", name_tr="Arjantin", group_letter="B")
    ger = Team.objects.create(tournament=t, code="GER", name_tr="Almanya", group_letter="B")

    s1 = BracketSlot.objects.create(
        tournament=t, stage=sg, position="GroupA-M1",
        scheduled_kickoff=_ist(2026, 6, 18, 20), home_team_actual=tur, away_team_actual=bra,
    )
    s2 = BracketSlot.objects.create(
        tournament=t, stage=sg, position="GroupB-M1",
        scheduled_kickoff=_ist(2026, 6, 19, 1), home_team_actual=arg, away_team_actual=ger,
    )

    User = get_user_model()
    u1 = User.objects.create_user(email="a@x.com", username="a@x.com", nickname="Ali")
    u2 = User.objects.create_user(email="b@x.com", username="b@x.com", nickname="Bora")
    for u in (u1, u2):
        SlotPrediction.objects.create(
            user=u, slot=s1, prediction_round=pr,
            home_team=tur, away_team=bra, home_score=2, away_score=1,
        )
        SlotPrediction.objects.create(
            user=u, slot=s2, prediction_round=pr,
            home_team=arg, away_team=ger, home_score=1, away_score=0,
        )
    return SimpleNamespace(t=t, s1=s1, s2=s2, u1=u1, u2=u2)


def _result(slot, home, away):
    return ActualResult.objects.create(slot=slot, home_score=home, away_score=away)


@pytest.mark.django_db
def test_morning_sends_to_all_recipients_with_potential(slate):
    call_command("send_daily_digest", "--mode", "morning", "--date", "2026-06-18")
    logs = EmailLog.objects.filter(kind=EmailLog.DAILY_MORNING, slate_date=SLATE)
    assert logs.count() == 2
    assert set(logs.values_list("email", flat=True)) == {"a@x.com", "b@x.com"}

    # Every revealed pick carries a best-case potential.
    matches = digest.build_morning_matches(slate.t, SLATE)
    assert len(matches) == 2
    assert all(p["potential"] is not None for m in matches for p in m["predictions"])


@pytest.mark.django_db
def test_evening_complete_sends_once_then_dedups(slate):
    _result(slate.s1, 2, 1)
    _result(slate.s2, 1, 0)
    assert digest.slate_is_complete(slate.t, SLATE) is True

    call_command("send_daily_digest", "--mode", "evening", "--date", "2026-06-18")
    assert EmailLog.objects.filter(kind=EmailLog.DAILY_EVENING, slate_date=SLATE).count() == 2

    # Second run on the same slate must not resend.
    call_command("send_daily_digest", "--mode", "evening", "--date", "2026-06-18")
    assert EmailLog.objects.filter(kind=EmailLog.DAILY_EVENING, slate_date=SLATE).count() == 2


@pytest.mark.django_db
def test_evening_incomplete_before_noon_waits(slate, monkeypatch):
    _result(slate.s1, 2, 1)  # only one of two results in
    monkeypatch.setattr(digest, "is_evening_final_poll", lambda now=None: False)

    call_command("send_daily_digest", "--mode", "evening", "--date", "2026-06-18")
    assert EmailLog.objects.filter(kind=EmailLog.DAILY_EVENING).count() == 0


@pytest.mark.django_db
def test_evening_noon_fallback_sends_partial(slate, monkeypatch):
    _result(slate.s1, 2, 1)  # s2 still missing → pending
    monkeypatch.setattr(digest, "is_evening_final_poll", lambda now=None: True)

    call_command("send_daily_digest", "--mode", "evening", "--date", "2026-06-18")
    assert EmailLog.objects.filter(kind=EmailLog.DAILY_EVENING, slate_date=SLATE).count() == 2

    matches = digest.build_evening_matches(slate.t, SLATE)
    pending = [m for m in matches if m["pending"]]
    assert len(pending) == 1
    assert pending[0]["result"] is None
