"""Daily digest core: slate windowing + per-recipient context building.

Two digests, both keyed off a 13:00 **Europe/Istanbul** boundary:

- **Morning** (sent 13:00): the slate that *opens* now — matches kicking off in
  ``[today 13:00, tomorrow 13:00)`` — with every player's locked prediction and
  its best-case ganyan potential ("en fazla N puan").
- **Evening** (polled hourly 08:00–12:00): the *previous* slate — matches that
  kicked off in ``[yesterday 13:00, today 13:00)`` — with the real earned
  GanyanScore payout, outcome badge, and the updated leaderboard.

The two digests therefore describe the *same* slate ~19h apart. ``slate_date``
is the date the window opens (D); the morning run on D and the evening run on
D+1 both resolve to slate D.

This module stays free of email/Command concerns so it can be unit-tested and
reused. The actual fan-out + dedup lives in the ``send_daily_digest`` command.
The per-slot gathering mirrors the public ``predictions_all`` / ``results_list``
views — same privacy gate, same "latest prediction per (slot,user)" rule.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.predictions.models import SlotPrediction
from apps.scoring.ganyan_bridge import potential_max_scores_for_slot
from apps.scoring.ganyan_leaderboard import leaderboard_for_tournament
from apps.scoring.models import GanyanScore
from apps.tournament.models import ActualResult, BracketSlot, Tournament

# The slate boundary: a slate runs from 13:00 one day to 13:00 the next, in
# Istanbul local time (TIME_ZONE = Europe/Istanbul, no DST).
SLATE_HOUR = 13

# Cron polls the evening digest 08:00–12:00; this is the last poll, after which
# we send whatever results are in (the "12:00 fallback").
EVENING_FINAL_HOUR = 12


# ---------- Windowing ----------


def _istanbul_boundary(d):
    """Aware datetime at SLATE_HOUR:00 Istanbul on date ``d``."""
    naive = datetime.combine(d, time(SLATE_HOUR, 0))
    return timezone.make_aware(naive, timezone.get_current_timezone())


def slate_window(slate_date) -> tuple:
    """[start, end) aware datetimes for the slate opening on ``slate_date``."""
    return _istanbul_boundary(slate_date), _istanbul_boundary(slate_date + timedelta(days=1))


def morning_slate_date(now=None):
    """The slate a 13:00 morning run opens — today (Istanbul) by the clock."""
    now = now or timezone.now()
    return timezone.localtime(now).date()


def evening_slate_date(now=None):
    """The slate an 08:00–12:00 evening run reports — yesterday's (Istanbul)."""
    now = now or timezone.now()
    return timezone.localtime(now).date() - timedelta(days=1)


def is_evening_final_poll(now=None) -> bool:
    """True on the last hourly evening poll (12:00 Istanbul) — send-anyway time."""
    now = now or timezone.now()
    return timezone.localtime(now).hour >= EVENING_FINAL_HOUR


def active_tournament():
    return Tournament.objects.filter(is_active=True).first()


def slate_slots(tournament, slate_date) -> list:
    """Window slots with both teams resolved, in kickoff order."""
    start, end = slate_window(slate_date)
    slots = (
        BracketSlot.objects
        .filter(
            tournament=tournament,
            scheduled_kickoff__gte=start,
            scheduled_kickoff__lt=end,
        )
        .select_related("stage", "home_team_actual", "away_team_actual", "result")
        .order_by("scheduled_kickoff")
    )
    return [s for s in slots if s.home_team_actual_id and s.away_team_actual_id]


def slate_is_complete(tournament, slate_date):
    """True/False if every slate match has a result; None if the slate is empty."""
    slots = slate_slots(tournament, slate_date)
    if not slots:
        return None
    with_result = set(
        ActualResult.objects
        .filter(slot_id__in=[s.id for s in slots])
        .values_list("slot_id", flat=True)
    )
    return all(s.id in with_result for s in slots)


# ---------- Prediction reveal gate (mirrors predictions.views.predictions_all) ----------


def _stages_still_editable(tournament) -> set:
    """Stage ids an OPEN prediction round can still edit — their predictions
    stay private. Canonical copy in apps.predictions.views._stages_still_editable.
    """
    now = timezone.now()
    open_stage_ids: set = set()
    for pr in tournament.prediction_rounds.prefetch_related("editable_stages"):
        if pr.deadline > now:
            open_stage_ids.update(s.id for s in pr.editable_stages.all())
    return open_stage_ids


def _predictions_public(slot, stages_still_editable) -> bool:
    if hasattr(slot, "result"):
        return True
    if slot.is_locked:
        return True
    return slot.stage_id not in stages_still_editable


def _latest_predictions_by_slot_user(slot_ids) -> dict:
    """{(slot_id, user_id): SlotPrediction} — the latest round's pick per pair."""
    qs = (
        SlotPrediction.objects
        .filter(slot_id__in=slot_ids)
        .select_related("user", "home_team", "away_team", "penalty_winner", "prediction_round")
        .order_by("slot_id", "user_id", "-prediction_round__order")
    )
    latest: dict = {}
    for p in qs:
        latest.setdefault((p.slot_id, p.user_id), p)
    return latest


def _nickname(user) -> str:
    return user.nickname or user.email


def _score_str(pred) -> str:
    return f"{pred.home_score}-{pred.away_score}"


# ---------- Morning ----------


def build_morning_matches(tournament, slate_date) -> list:
    """Recipient-independent match list for the morning digest.

    Each match: {home, away, kickoff, predictions:[{user_id, nickname,
    prediction, potential}]} — predictions sorted by nickname. Only matches
    whose predictions are already public are included (privacy gate).
    """
    slots = slate_slots(tournament, slate_date)
    stages_editable = _stages_still_editable(tournament)
    latest = _latest_predictions_by_slot_user([s.id for s in slots])

    matches = []
    for slot in slots:
        if not _predictions_public(slot, stages_editable):
            continue
        slot_preds = sorted(
            (p for (sid, _uid), p in latest.items() if sid == slot.id),
            key=lambda p: _nickname(p.user).lower(),
        )
        if not slot_preds:
            continue
        potentials = potential_max_scores_for_slot(slot, slot_preds)
        predictions = [
            {
                "user_id": p.user_id,
                "nickname": _nickname(p.user),
                "prediction": _score_str(p),
                "potential": potentials.get(p.user_id),
            }
            for p in slot_preds
        ]
        matches.append({
            "home": slot.home_team_actual.name_tr,
            "away": slot.away_team_actual.name_tr,
            "kickoff": slot.scheduled_kickoff,
            "predictions": predictions,
        })
    return matches


def morning_context(tournament, slate_date, recipient, matches=None) -> dict:
    """Per-recipient morning context (is_self flags + missing-pick nudge)."""
    if matches is None:
        matches = build_morning_matches(tournament, slate_date)

    out_matches = []
    has_missing = False
    for m in matches:
        predicted_ids = {p["user_id"] for p in m["predictions"]}
        if recipient.id not in predicted_ids:
            has_missing = True
        out_matches.append({
            **m,
            "predictions": [
                {**p, "is_self": p["user_id"] == recipient.id}
                for p in m["predictions"]
            ],
        })
    return {
        "nickname": _nickname(recipient),
        "site_url": settings.SITE_URL,
        "all_predictions_url": f"{settings.SITE_URL}/predictions/all/",
        "date": slate_date,
        "matches": out_matches,
        "has_missing": has_missing,
    }


# ---------- Evening ----------


def build_evening_matches(tournament, slate_date) -> list:
    """Recipient-independent match list for the evening digest.

    Played match: {pending:False, home, away, result, predictions:[{user_id,
    nickname, prediction, score, outcome}]} ordered by payout desc. Not-yet-
    scored match (12:00 fallback): {pending:True, ..., predictions:[{user_id,
    nickname, prediction}]} — no score, rendered as "sonuç bekleniyor".
    """
    slots = slate_slots(tournament, slate_date)
    slot_ids = [s.id for s in slots]
    actuals = {a.slot_id: a for a in ActualResult.objects.filter(slot_id__in=slot_ids)}

    score_rows = (
        GanyanScore.objects
        .filter(slot_id__in=slot_ids)
        .exclude(outcome=GanyanScore.NO_RESULT)
        .select_related("user", "effective_round")
    )
    scores_by_slot: dict = {}
    for s in score_rows:
        scores_by_slot.setdefault(s.slot_id, []).append(s)

    latest = _latest_predictions_by_slot_user(slot_ids)

    # Pick the prediction whose score is shown (effective round, else latest).
    preds_by_us: dict = {}
    for (sid, uid), p in latest.items():
        preds_by_us[(uid, sid)] = p
    all_round_preds = (
        SlotPrediction.objects
        .filter(slot_id__in=slot_ids)
        .select_related("home_team", "away_team", "prediction_round")
        .order_by("user_id", "slot_id", "-prediction_round__order")
    )
    buckets: dict = {}
    for p in all_round_preds:
        buckets.setdefault((p.user_id, p.slot_id), []).append(p)

    def pick(uid, sid, eff):
        bucket = buckets.get((uid, sid), [])
        if not bucket:
            return None
        if eff is not None:
            for p in bucket:
                if p.prediction_round_id == eff:
                    return p
        return bucket[0]

    matches = []
    for slot in slots:
        actual = actuals.get(slot.id)
        if actual is None:
            # Pending — surface everyone's locked pick, no score yet.
            slot_preds = sorted(
                (p for (sid, _uid), p in latest.items() if sid == slot.id),
                key=lambda p: _nickname(p.user).lower(),
            )
            matches.append({
                "pending": True,
                "home": slot.home_team_actual.name_tr,
                "away": slot.away_team_actual.name_tr,
                "result": None,
                "predictions": [
                    {"user_id": p.user_id, "nickname": _nickname(p.user),
                     "prediction": _score_str(p)}
                    for p in slot_preds
                ],
            })
            continue

        rows = sorted(
            scores_by_slot.get(slot.id, []),
            key=lambda s: (-s.total, _nickname(s.user).lower()),
        )
        predictions = []
        for s in rows:
            p = pick(s.user_id, slot.id, s.effective_round_id)
            predictions.append({
                "user_id": s.user_id,
                "nickname": _nickname(s.user),
                "prediction": _score_str(p) if p else None,
                "score": s.total,
                "outcome": s.outcome,
            })
        matches.append({
            "pending": False,
            "home": slot.home_team_actual.name_tr,
            "away": slot.away_team_actual.name_tr,
            "result": f"{actual.home_score}-{actual.away_score}",
            "predictions": predictions,
        })
    return matches


def build_evening_leaderboard(tournament, slate_date):
    """Returns (leaderboard_rows_base, daily_by_user).

    Rows are recipient-independent {rank, user_id, nickname, total, daily}.
    `daily` is the points earned on this slate's matches.
    """
    entries = leaderboard_for_tournament(tournament)

    slots = slate_slots(tournament, slate_date)
    daily_by_user: dict = {}
    for s in (
        GanyanScore.objects
        .filter(slot_id__in=[s.id for s in slots])
        .exclude(outcome=GanyanScore.NO_RESULT)
        .only("user_id", "total")
    ):
        daily_by_user[s.user_id] = daily_by_user.get(s.user_id, Decimal("0")) + s.total

    rows = [
        {
            "rank": e.rank,
            "user_id": e.user.id,
            "nickname": e.nickname,
            "total": e.total,
            "daily": daily_by_user.get(e.user.id, Decimal("0")),
        }
        for e in entries
    ]
    return rows, daily_by_user


def evening_context(tournament, slate_date, recipient, *, matches=None, leaderboard_base=None,
                    daily_by_user=None) -> dict:
    """Per-recipient evening context (is_self flags + own daily total)."""
    if matches is None:
        matches = build_evening_matches(tournament, slate_date)
    if leaderboard_base is None or daily_by_user is None:
        leaderboard_base, daily_by_user = build_evening_leaderboard(tournament, slate_date)

    out_matches = [
        {
            **m,
            "predictions": [
                {**p, "is_self": p["user_id"] == recipient.id}
                for p in m["predictions"]
            ],
        }
        for m in matches
    ]
    leaderboard = [
        {**row, "is_self": row["user_id"] == recipient.id}
        for row in leaderboard_base
    ]
    return {
        "nickname": _nickname(recipient),
        "site_url": settings.SITE_URL,
        "date": slate_date,
        "finished_matches": out_matches,
        "daily_points": daily_by_user.get(recipient.id, Decimal("0")),
        "leaderboard": leaderboard,
        "leaderboard_url": f"{settings.SITE_URL}/leaderboard/",
    }


# ---------- Recipients + subjects/text ----------


def digest_recipients() -> list:
    """Active users with an email address."""
    User = get_user_model()
    return list(
        User.objects.filter(is_active=True).exclude(email="").order_by("id")
    )


def morning_subject() -> str:
    return "☀️ Bugünün maçları — ekiptahmin.com"


def evening_subject() -> str:
    return "🌙 Günün özeti — ekiptahmin.com"


def morning_text(context) -> str:
    n = len(context["matches"])
    return (
        f"Selam {context['nickname']}! Bugün {n} maç oynanacak, tüm tahminler kilitlendi.\n"
        f"Herkesin tahminini gör: {context['all_predictions_url']}"
    )


def evening_text(context) -> str:
    return (
        f"Selam {context['nickname']}! Günün özeti hazır — bugün {context['daily_points']} puan.\n"
        f"Güncel sıralama: {context['leaderboard_url']}"
    )
