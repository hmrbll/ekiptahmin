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

from apps.liveresults.models import MatchSync
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


def _api_result(slot, home, away, *, status=MatchSync.STATUS_IN_PLAY, finalized=False):
    """A live-sync result: ActualResult(source=API) + its MatchSync bookkeeping.
    Defaults to a still-running match (IN_PLAY, not finalized) — i.e. a running
    score that must NOT count as a final result for the evening digest."""
    ar = ActualResult.objects.create(
        slot=slot, home_score=home, away_score=away, source=ActualResult.SOURCE_API,
    )
    MatchSync.objects.create(
        slot=slot, external_id=f"ext-{slot.id}", status=status, finalized=finalized,
    )
    return ar


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
def test_morning_knockout_one_row_per_round_and_drops_wrong_matchup():
    """Knockout card mirrors predictions_all: a user who predicted the actual
    fixture in several rounds gets one row per round (each tagged with its
    weight and its own potential); picks on a different matchup are dropped."""
    t = Tournament.objects.create(
        name="KO Cup", slug="ko-cup",
        start_date=date(2026, 6, 1), end_date=date(2026, 7, 1), is_active=True,
    )
    r32 = Stage.objects.create(
        tournament=t, kind=Stage.R32, order=1,
        points_exact=6, points_diff=4, points_result=2, penalty_loser_pct=Decimal("0.60"),
    )
    pre = PredictionRound.objects.create(
        tournament=t, name="Pre-turnuva", order=0,
        deadline=timezone.now() - timedelta(days=2), weight=Decimal("1.00"),
    )
    grup = PredictionRound.objects.create(
        tournament=t, name="Grup sonrası", order=1,
        deadline=timezone.now() - timedelta(days=1), weight=Decimal("0.85"),
    )
    pre.editable_stages.set([r32])
    grup.editable_stages.set([r32])

    ned = Team.objects.create(tournament=t, code="NED", name_tr="Hollanda", group_letter="A")
    mar = Team.objects.create(tournament=t, code="MAR", name_tr="Fas", group_letter="B")
    swe = Team.objects.create(tournament=t, code="SWE", name_tr="İsveç", group_letter="C")

    slot = BracketSlot.objects.create(
        tournament=t, stage=r32, position="R32-1",
        scheduled_kickoff=_ist(2026, 6, 18, 20),
        home_team_actual=ned, away_team_actual=mar,
    )

    User = get_user_model()
    ali = User.objects.create_user(email="ali@x.com", username="ali@x.com", nickname="Ali")
    bora = User.objects.create_user(email="bora@x.com", username="bora@x.com", nickname="Bora")

    # Ali predicted the real NED–MAR fixture in BOTH rounds → two rows.
    SlotPrediction.objects.create(user=ali, slot=slot, prediction_round=pre,
                                  home_team=ned, away_team=mar, home_score=3, away_score=2)
    SlotPrediction.objects.create(user=ali, slot=slot, prediction_round=grup,
                                  home_team=ned, away_team=mar, home_score=2, away_score=1)
    # Bora guessed a wrong matchup pre-tournament (dropped) then re-picked the
    # real fixture after groups (kept).
    SlotPrediction.objects.create(user=bora, slot=slot, prediction_round=pre,
                                  home_team=ned, away_team=swe, home_score=1, away_score=0)
    SlotPrediction.objects.create(user=bora, slot=slot, prediction_round=grup,
                                  home_team=ned, away_team=mar, home_score=1, away_score=0)

    matches = digest.build_morning_matches(t, SLATE)
    assert len(matches) == 1
    rows = matches[0]["predictions"]

    # Ali twice (1.00 then 0.85), Bora once (0.85) — the wrong-matchup pre pick is gone.
    assert [(r["nickname"], str(r["round_weight"]), r["prediction"]) for r in rows] == [
        ("Ali", "1.00", "3-2"),
        ("Ali", "0.85", "2-1"),
        ("Bora", "0.85", "1-0"),
    ]
    # Every shown row is a real fixture pick, so each carries its own potential.
    assert all(r["potential"] is not None for r in rows)


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
def test_running_api_score_is_not_final(slate, monkeypatch):
    """The 08:00 regression: a match still IN_PLAY has a running ActualResult
    but is NOT final, so the slate is incomplete and the digest must wait."""
    _result(slate.s1, 2, 1)                               # final (manual)
    _api_result(slate.s2, 0, 3)                           # running 0-3, IN_PLAY
    monkeypatch.setattr(digest, "is_evening_final_poll", lambda now=None: False)

    assert digest.slate_is_complete(slate.t, SLATE) is False
    call_command("send_daily_digest", "--mode", "evening", "--date", "2026-06-18")
    assert EmailLog.objects.filter(kind=EmailLog.DAILY_EVENING).count() == 0

    # ...and it shows as pending (no running score leaked into the result line).
    pending = [m for m in digest.build_evening_matches(slate.t, SLATE) if m["pending"]]
    assert len(pending) == 1
    assert pending[0]["result"] is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status, finalized",
    [(MatchSync.STATUS_FINISHED, False), (MatchSync.STATUS_IN_PLAY, True)],
)
def test_final_api_score_completes_slate(slate, status, finalized):
    """An API result counts as final once FINISHED is captured — either via the
    status (FINISHED) or finalize_stale_syncs setting the flag on a stuck row."""
    _result(slate.s1, 2, 1)
    _api_result(slate.s2, 0, 4, status=status, finalized=finalized)
    assert digest.slate_is_complete(slate.t, SLATE) is True


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


@pytest.mark.django_db
def test_evening_beyond_90_result_uses_effective_score(slate):
    """The result line shows the effective (120') score with the ET/shootout
    note — not the raw 90' draw (R32-9 BEL-SEN read as "2-2" in the digest
    while the site said 3-2 uzatma)."""
    # s1: won 3-2 in extra time. s2: 1-1 draw, penalties 3-4 to the away side.
    ActualResult.objects.create(
        slot=slate.s1, home_score=2, away_score=2,
        went_to_extra_time=True, home_score_aet=3, away_score_aet=2,
    )
    ActualResult.objects.create(
        slot=slate.s2, home_score=1, away_score=1,
        went_to_extra_time=True, went_to_penalties=True,
        home_score_aet=1, away_score_aet=1,
        home_penalties=3, away_penalties=4,
        penalty_winner=slate.s2.away_team_actual,
    )

    by_home = {m["home"]: m for m in digest.build_evening_matches(slate.t, SLATE)}
    et = by_home[slate.s1.home_team_actual.name_tr]
    pen = by_home[slate.s2.home_team_actual.name_tr]

    assert (et["result"], et["result_note"]) == ("3-2", "uzatma")
    assert (pen["result"], pen["result_note"]) == (
        "1-1", f"pen: {slate.s2.away_team_actual.code} 3-4"
    )


@pytest.mark.django_db
def test_evening_regulation_result_has_no_note(slate):
    _result(slate.s1, 2, 1)
    _result(slate.s2, 1, 0)
    for m in digest.build_evening_matches(slate.t, SLATE):
        assert m["result_note"] is None
