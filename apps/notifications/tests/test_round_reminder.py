"""Tests for send_round_reminder: recipient targeting (zero predictions in the
round), per-user dedup via EmailLog, open/deadline guards, and the humanized
time-left strings. Rendering details live in the templates; here we exercise
the send/targeting logic.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.notifications.management.commands.send_round_reminder import _time_left_display
from apps.notifications.models import EmailLog
from apps.predictions.models import SlotPrediction
from apps.tournament.models import BracketSlot, PredictionRound, Stage, Team, Tournament


# ------------------------------------------------------------- time display


@pytest.mark.parametrize("minutes,expected", [
    (45, "45 dakika"),
    (89, "89 dakika"),
    (90, "1,5 saat"),
    (170, "3 saat"),
    (450, "7,5 saat"),
    (1050, "17,5 saat"),
])
def test_time_left_display(minutes, expected):
    assert _time_left_display(timedelta(minutes=minutes)) == expected


# -------------------------------------------------------------- integration


@pytest.fixture
def reminder_round(db):
    """An open round (future deadline, opens_at passed, no stage dependency)
    with two predictable slots and three users: one fully predicted, one
    partial, two with nothing (one of those undeliverable)."""
    t = Tournament.objects.create(
        name="Test Cup", slug="test-cup",
        start_date=date(2026, 6, 1), end_date=date(2026, 7, 31), is_active=True,
    )
    sg = Stage.objects.create(
        tournament=t, kind=Stage.GROUP, order=0,
        points_exact=6, points_diff=4, points_result=2, penalty_loser_pct=Decimal("0.60"),
    )
    rnd = PredictionRound.objects.create(
        tournament=t, name="R32 sonrası", order=0,
        deadline=timezone.now() + timedelta(hours=8),
        opens_at=timezone.now() - timedelta(hours=1),
        weight=Decimal("0.75"),
    )
    rnd.editable_stages.set([sg])

    tur = Team.objects.create(tournament=t, code="TUR", name_tr="Türkiye", group_letter="A")
    bra = Team.objects.create(tournament=t, code="BRA", name_tr="Brezilya", group_letter="A")
    s1 = BracketSlot.objects.create(
        tournament=t, stage=sg, position="M1",
        scheduled_kickoff=timezone.now() + timedelta(hours=9),
        home_team_actual=tur, away_team_actual=bra,
    )
    s2 = BracketSlot.objects.create(
        tournament=t, stage=sg, position="M2",
        scheduled_kickoff=timezone.now() + timedelta(hours=10),
        home_team_actual=bra, away_team_actual=tur,
    )

    User = get_user_model()
    done = User.objects.create_user(email="done@x.com", username="done@x.com", nickname="Done")
    partial = User.objects.create_user(email="partial@x.com", username="partial@x.com", nickname="Partial")
    User.objects.create_user(email="idle@x.com", username="idle@x.com", nickname="Idle")
    bounced = User.objects.create_user(email="bounced@x.com", username="bounced@x.com", nickname="Bounced")
    bounced.email_undeliverable = True
    bounced.save(update_fields=["email_undeliverable"])

    for slot in (s1, s2):
        SlotPrediction.objects.create(
            user=done, slot=slot, prediction_round=rnd,
            home_team=slot.home_team_actual, away_team=slot.away_team_actual,
            home_score=2, away_score=1,
        )
    SlotPrediction.objects.create(
        user=partial, slot=s1, prediction_round=rnd,
        home_team=s1.home_team_actual, away_team=s1.away_team_actual,
        home_score=1, away_score=1,
    )
    return rnd


def test_targets_only_zero_prediction_deliverable_users(reminder_round, mailoutbox):
    call_command("send_round_reminder", "--round-id", reminder_round.pk)
    assert [m.to for m in mailoutbox] == [["idle@x.com"]]
    log = EmailLog.objects.get(kind=EmailLog.ROUND_REMINDER)
    assert log.email == "idle@x.com"
    assert log.status == EmailLog.SENT
    assert log.slate_date == timezone.localtime(reminder_round.deadline).date()


def test_rerun_skips_already_reminded(reminder_round, mailoutbox):
    call_command("send_round_reminder", "--round-id", reminder_round.pk)
    call_command("send_round_reminder", "--round-id", reminder_round.pk)
    assert len(mailoutbox) == 1
    assert EmailLog.objects.filter(kind=EmailLog.ROUND_REMINDER).count() == 1


def test_dry_run_sends_and_logs_nothing(reminder_round, mailoutbox):
    call_command("send_round_reminder", "--round-id", reminder_round.pk, "--dry-run")
    assert mailoutbox == []
    assert EmailLog.objects.filter(kind=EmailLog.ROUND_REMINDER).count() == 0


def test_round_not_open_aborts_unless_forced(reminder_round, mailoutbox):
    reminder_round.opens_at = timezone.now() + timedelta(hours=1)
    reminder_round.save(update_fields=["opens_at"])
    with pytest.raises(CommandError, match="not open"):
        call_command("send_round_reminder", "--round-id", reminder_round.pk)
    assert mailoutbox == []
    call_command("send_round_reminder", "--round-id", reminder_round.pk, "--force")
    assert len(mailoutbox) == 1


def test_passed_deadline_always_aborts(reminder_round, mailoutbox):
    reminder_round.deadline = timezone.now() - timedelta(minutes=5)
    reminder_round.save(update_fields=["deadline"])
    with pytest.raises(CommandError, match="deadline has passed"):
        call_command("send_round_reminder", "--round-id", reminder_round.pk, "--force")
    assert mailoutbox == []


def test_missing_round_aborts(db):
    with pytest.raises(CommandError, match="does not exist"):
        call_command("send_round_reminder", "--round-id", 999)
