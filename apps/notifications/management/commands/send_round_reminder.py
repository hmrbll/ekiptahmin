"""One-shot 'complete your predictions' reminder for a prediction round.

Targets the digest recipient base (active + deliverable) minus anyone who has
made at least one prediction in the round — partial predictors are left alone,
the nudge is for people who haven't started. Knockout context: earlier-round
picks only score on the actual fixture (strict home+away), so once the real
matchups are known this round is the recipient's chance to predict them.

Dedup: one reminder per (user, round) via EmailLog rows with
``kind=ROUND_REMINDER`` and ``slate_date=<deadline's Istanbul date>`` — a
re-run (e.g. a scheduled-task retry after a transient failure) only mails the
users the first run missed.

    python manage.py send_round_reminder --round-id 3 --dry-run
    python manage.py send_round_reminder --round-id 3
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.notifications.digest import digest_recipients
from apps.notifications.emails import send_logged
from apps.notifications.models import EmailLog
from apps.predictions.models import SlotPrediction
from apps.tournament.models import BracketSlot, PredictionRound


def _time_left_display(remaining) -> str:
    """Humanized Turkish time-left string: '45 dakika', '3 saat', '7,5 saat'."""
    minutes = int(remaining.total_seconds() // 60)
    if minutes < 90:
        return f"{minutes} dakika"
    half_hours = round(minutes / 30)
    if half_hours % 2 == 0:
        return f"{half_hours // 2} saat"
    return f"{half_hours // 2},5 saat"


def _day_word(deadline_local) -> str:
    today = timezone.localdate()
    if deadline_local.date() == today:
        return "bugün"
    if (deadline_local.date() - today).days == 1:
        return "yarın"
    return deadline_local.strftime("%d.%m")


class Command(BaseCommand):
    help = (
        "Email everyone who has made NO predictions in the given round a "
        "reminder to fill their bracket before the deadline."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--round-id", type=int, required=True,
            help="PredictionRound pk to remind for.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Render every recipient's mail but don't send or log.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Send even if the round hasn't opened yet (a passed deadline "
                 "still aborts — reminding for a locked round is never right).",
        )

    def handle(self, *args, **opts):
        try:
            rnd = PredictionRound.objects.get(pk=opts["round_id"])
        except PredictionRound.DoesNotExist:
            raise CommandError(f"PredictionRound id={opts['round_id']} does not exist.")

        now = timezone.now()
        if now >= rnd.deadline:
            raise CommandError(f"Round '{rnd.name}' deadline has passed ({rnd.deadline:%Y-%m-%d %H:%M} UTC) — not sending.")
        if not rnd.is_open and not opts["force"]:
            raise CommandError(
                f"Round '{rnd.name}' is not open yet (opens_at or stage dependency "
                f"unmet) — a reminder would link to a closed wizard. Retry once it "
                f"opens, or pass --force if you know better."
            )

        slot_count = BracketSlot.objects.filter(stage__in=rnd.editable_stages.all()).count()
        predicted_ids = set(
            SlotPrediction.objects.filter(prediction_round=rnd).values_list("user_id", flat=True)
        )
        slate_date = timezone.localtime(rnd.deadline).date()
        already_ids = set(
            EmailLog.objects.filter(
                kind=EmailLog.ROUND_REMINDER,
                slate_date=slate_date,
                status__in=EmailLog.HANDLED_STATUSES,
            ).values_list("user_id", flat=True)
        )
        recipients = [
            u for u in digest_recipients()
            if u.id not in predicted_ids and u.id not in already_ids
        ]

        deadline_local = timezone.localtime(rnd.deadline)
        time_left = _time_left_display(rnd.deadline - now)
        subject = (
            f"⏳ {rnd.name} kapanıyor ({_day_word(deadline_local)} {deadline_local:%H:%M}) "
            f"— tahminlerini yapmadın"
        )
        base_ctx = {
            "round_name": rnd.name,
            "round_weight": rnd.weight,
            "deadline": rnd.deadline,
            "pending_count": slot_count,
            "time_left_display": time_left,
            "predict_url": f"{settings.SITE_URL}{reverse('predict_round_entry', args=[rnd.pk])}",
            "site_url": settings.SITE_URL,
        }

        self.stdout.write(
            f"Round reminder '{rnd.name}' (deadline {deadline_local:%d.%m %H:%M}, {time_left} left): "
            f"{len(predicted_ids)} users already predicted, {len(already_ids)} already reminded "
            f"→ {len(recipients)} recipients{' (dry-run)' if opts['dry_run'] else ''}"
        )

        sent = failed = 0
        for user in recipients:
            ctx = {**base_ctx, "nickname": user.nickname or user.email.split("@")[0]}
            html = render_to_string("emails/round_deadline.html", ctx)
            body = render_to_string("emails/round_deadline.txt", ctx)
            if opts["dry_run"]:
                continue
            log = send_logged(
                subject=subject, body=body, html=html,
                recipient=user.email, kind=EmailLog.ROUND_REMINDER,
                user=user, slate_date=slate_date,
            )
            if log.status == EmailLog.FAILED:
                failed += 1
                self.stderr.write(f"  send failed → {user.email}: {log.error}")
            else:
                sent += 1

        if opts["dry_run"]:
            self.stdout.write(f"Dry-run OK — rendered {len(recipients)} mails, none sent.")
        else:
            self.stdout.write(f"Done — sent {sent}, failed {failed}.")
            if failed and not sent:
                raise CommandError("Every send failed — check the email backend/API key.")
