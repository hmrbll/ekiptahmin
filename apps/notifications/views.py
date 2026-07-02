"""Staff-only preview views for email templates.

Each preview renders a real template with realistic sample data so the
daily-digest cron job (still TODO — render.yaml cron service + a
send_daily_digest management command) can use the same templates without
rework. Round-opened / deadline-reminder mails were dropped by decision —
the only scheduled mails are the morning + evening daily digests.

Scoring shape note: the daily digests speak the parimutuel ganyan language.
Points are Decimal payouts (pool / winners), not fixed chips. The morning
digest shows each pick's best-case potential (ganyan.potential_max_scores);
the evening digest shows the real earned GanyanScore.total plus the best-tier
outcome badge (keys match GanyanScore.outcome / scoring.views._OUTCOME_BADGE).
"""
from decimal import Decimal

from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from .models import EmailLog

EMAIL_LOG_PAGE_SIZE = 50

# (slug, template_name, display label)
EMAIL_PREVIEWS = [
    ("invite_welcome", "emails/invite_welcome.html", "Davet — hoş geldin"),
    ("magic_link_signup", "emails/magic_link_signup.html", "Magic link — kayıt"),
    ("magic_link_login", "emails/magic_link_login.html", "Magic link — giriş"),
    ("daily_morning", "emails/daily_morning.html", "Daily — sabah"),
    ("daily_evening", "emails/daily_evening.html", "Daily — akşam"),
]


# Realistic dummy user roster shared across daily previews
_SAMPLE_ROSTER = ["Hemre", "Ali", "Zeynep", "Emre", "Selin", "Cem", "Defne"]


def _sample_context(slug: str, request=None) -> dict:
    now = timezone.now()
    nickname = "Hemre"
    confirm_url = "https://ekiptahmin.com/auth/confirm/?t=sample-token"
    site_url = (
        request.build_absolute_uri("/").rstrip("/") if request else "https://ekiptahmin.com"
    )

    base = {"site_url": site_url, "nickname": nickname}

    if slug == "invite_welcome":
        return {**base, "invite_url": f"{site_url}/invite/sample-code/"}

    if slug in ("magic_link_signup", "magic_link_login"):
        return {**base, "confirm_url": confirm_url}

    if slug == "daily_morning":
        def preds(rows):
            # rows: (prediction, potential_max). `potential` is the best-case
            # ganyan payout if the match ends exactly as predicted — None when
            # there is no prediction (or a pick that can't score the fixture).
            # Production sender computes it via ganyan.potential_max_scores.
            return [
                {"nickname": n, "prediction": v, "is_self": n == nickname,
                 "potential": pot}
                for n, (v, pot) in zip(_SAMPLE_ROSTER, rows)
            ]
        return {
            **base,
            "date": now,
            "matches": [
                {
                    "home": "Meksika", "away": "Kanada",
                    "kickoff": now.replace(hour=18, minute=0),
                    "predictions": preds([
                        ("2-1", Decimal("125.00")), ("1-1", Decimal("300.00")),
                        ("3-0", Decimal("225.00")), ("2-0", Decimal("225.00")),
                        ("1-2", Decimal("300.00")), (None, None),
                        ("2-1", Decimal("125.00")),
                    ]),
                },
                {
                    "home": "ABD", "away": "Türkiye",
                    "kickoff": now.replace(hour=21, minute=0),
                    "predictions": preds([
                        (None, None), ("0-2", Decimal("200.00")),
                        ("1-1", Decimal("300.00")), ("0-1", Decimal("150.00")),
                        ("2-2", Decimal("300.00")), ("0-3", Decimal("200.00")),
                        ("1-2", Decimal("150.00")),
                    ]),
                },
                {
                    "home": "Suudi Arabistan", "away": "Fas",
                    "kickoff": now.replace(hour=23, minute=30),
                    "predictions": preds([
                        ("0-1", Decimal("150.00")), ("1-2", Decimal("150.00")),
                        ("0-2", Decimal("200.00")), (None, None),
                        ("1-1", Decimal("300.00")), ("0-1", Decimal("150.00")),
                        ("2-1", Decimal("300.00")),
                    ]),
                },
            ],
            "has_missing": True,
            "all_predictions_url": f"{site_url}/predictions/all/",
        }

    if slug == "daily_evening":
        def preds(rows):
            # rows: (prediction, score, outcome). `score` is the real ganyan
            # payout (Decimal); `outcome` is the best-tier key matching
            # GanyanScore.outcome (exact/diff/result/penalty/miss/no_prediction).
            return [
                {"nickname": n, "prediction": v, "score": s, "outcome": o,
                 "is_self": n == nickname}
                for n, (v, s, o) in zip(_SAMPLE_ROSTER, rows)
            ]
        return {
            **base,
            "date": now,
            "finished_matches": [
                {
                    "home": "Meksika", "away": "Kanada", "result": "2-1", "result_note": None,
                    "pending": False,
                    "predictions": preds([
                        ("2-1", Decimal("125.00"), "exact"),
                        ("1-1", Decimal("0"), "miss"),
                        ("3-0", Decimal("25.00"), "result"),
                        ("2-0", Decimal("25.00"), "result"),
                        ("1-2", Decimal("0"), "miss"),
                        (None, Decimal("0"), "no_prediction"),
                        ("2-1", Decimal("125.00"), "exact"),
                    ]),
                },
                {
                    # Knockout decided on penalties: result is the 120' draw, the
                    # shootout rides in result_note (same as the site's cards).
                    "home": "ABD", "away": "Türkiye", "result": "1-1",
                    "result_note": "pen: TUR 4-3", "pending": False,
                    "predictions": preds([
                        ("1-1", Decimal("120.00"), "exact"),
                        ("1-1", Decimal("120.00"), "exact"),
                        ("2-2", Decimal("20.00"), "diff"),
                        ("0-0", Decimal("20.00"), "diff"),
                        ("0-1", Decimal("15.00"), "penalty"),
                        ("0-2", Decimal("0"), "miss"),
                        ("2-1", Decimal("0"), "miss"),
                    ]),
                },
                {
                    # 12:00 fallback illustration: results not yet entered →
                    # rendered as "sonuç bekleniyor", picks shown without scores.
                    "home": "İspanya", "away": "Portekiz", "result": None,
                    "result_note": None, "pending": True,
                    "predictions": preds([
                        ("1-0", Decimal("0"), "miss"),
                        ("2-1", Decimal("0"), "miss"),
                        ("1-1", Decimal("0"), "miss"),
                        ("0-0", Decimal("0"), "miss"),
                        ("2-0", Decimal("0"), "miss"),
                        ("1-2", Decimal("0"), "miss"),
                        ("3-1", Decimal("0"), "miss"),
                    ]),
                },
            ],
            "daily_points": Decimal("245.00"),
            "leaderboard": [
                {"rank": 1, "nickname": "Hemre",  "total": Decimal("688.90"), "daily": Decimal("245.00"), "is_self": True},
                {"rank": 2, "nickname": "Defne",  "total": Decimal("640.15"), "daily": Decimal("145.00"), "is_self": False},
                {"rank": 3, "nickname": "Ali",    "total": Decimal("598.40"), "daily": Decimal("120.00"), "is_self": False},
                {"rank": 4, "nickname": "Selin",  "total": Decimal("540.00"), "daily": Decimal("0"), "is_self": False},
                {"rank": 5, "nickname": "Emre",   "total": Decimal("502.75"), "daily": Decimal("45.00"), "is_self": False},
                {"rank": 6, "nickname": "Zeynep", "total": Decimal("455.20"), "daily": Decimal("0"), "is_self": False},
                {"rank": 7, "nickname": "Cem",    "total": Decimal("390.60"), "daily": Decimal("20.00"), "is_self": False},
            ],
            "leaderboard_url": f"{site_url}/leaderboard/",
        }

    return base


@staff_member_required
def email_log_list(request):
    """Staff-only audit of every logged outbound mail (newest first).

    Filterable by status and kind via GET params; paginated. Powers the
    /ops/emails/ tracking page (Faz 1.2). Only mails sent through
    `send_logged` appear here — see apps.notifications.emails.
    """
    qs = EmailLog.objects.select_related("user").order_by("-created_at")
    status = request.GET.get("status", "")
    kind = request.GET.get("kind", "")
    if status:
        qs = qs.filter(status=status)
    if kind:
        qs = qs.filter(kind=kind)

    paginator = Paginator(qs, EMAIL_LOG_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "notifications/email_log_list.html", {
        "page": page,
        "total": paginator.count,
        "status": status,
        "kind": kind,
        "status_choices": EmailLog.STATUS_CHOICES,
        "kind_choices": EmailLog.KIND_CHOICES,
    })


@staff_member_required
def preview_index(request):
    return render(request, "notifications/preview_index.html", {"previews": EMAIL_PREVIEWS})


@staff_member_required
def preview_detail(request, slug: str):
    entry = next((p for p in EMAIL_PREVIEWS if p[0] == slug), None)
    if entry is None:
        raise Http404
    _, template_name, _label = entry
    context = _sample_context(slug, request=request)
    return render(request, template_name, context)
