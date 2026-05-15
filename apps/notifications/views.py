"""Staff-only preview views for email templates.

Each preview renders a real template with realistic sample data so the
production cron jobs (Dalga 1.3) can use the same templates without rework.
"""
from datetime import timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

# (slug, template_name, display label)
EMAIL_PREVIEWS = [
    ("invite_welcome", "emails/invite_welcome.html", "Davet — hoş geldin"),
    ("magic_link_signup", "emails/magic_link_signup.html", "Magic link — kayıt"),
    ("magic_link_login", "emails/magic_link_login.html", "Magic link — giriş"),
    ("round_opened_pre", "emails/round_opened.html", "Round açıldı — pre-turnuva"),
    ("round_opened_group", "emails/round_opened.html", "Round açıldı — grup aşaması"),
    ("round_opened_knockout", "emails/round_opened.html", "Round açıldı — eleme"),
    ("round_opened_final", "emails/round_opened.html", "Round açıldı — final"),
    ("round_deadline_24h", "emails/round_deadline.html", "Reminder — son 24 saat"),
    ("round_deadline_12h", "emails/round_deadline.html", "Reminder — son 12 saat"),
    ("round_deadline_6h", "emails/round_deadline.html", "Reminder — son 6 saat"),
    ("round_deadline_30min", "emails/round_deadline.html", "Reminder — son 30 dakika"),
    ("daily_morning", "emails/daily_morning.html", "Daily — sabah"),
    ("daily_evening", "emails/daily_evening.html", "Daily — akşam"),
]


# Realistic dummy user roster shared across daily previews
_SAMPLE_ROSTER = ["Hemre", "Ali", "Zeynep", "Emre", "Selin", "Cem", "Defne"]


def _round_opened_variant(kind: str, now) -> dict:
    """Map a round kind to its display attributes. Production senders map
    real Round model objects (group_stage, ko_round_of_16, ...) to the same
    shape — this preview hardcodes one per variant for design review."""
    deadline = now + timedelta(hours=26)
    variants = {
        "pre": {
            "round_kicker": "Pre-turnuva",
            "round_emoji": "🏆",
            "round_title": "Turnuva öncesi büyük tahminler",
            "round_tagline": "Şampiyon kim? Gol kralı kim? En sürpriz takım hangisi?",
            "prediction_count": 5,
            "deadline": deadline,
        },
        "group": {
            "round_kicker": "Grup aşaması · 1. tur",
            "round_emoji": "⚽",
            "round_title": "Grup aşaması başlıyor",
            "round_tagline": "İlk düdük çalmadan tahminlerini gönder. 32 takım, 16 maç.",
            "prediction_count": 16,
            "deadline": deadline,
        },
        "knockout": {
            "round_kicker": "Son 16 · eleme",
            "round_emoji": "🔥",
            "round_title": "Eleme başladı",
            "round_tagline": "Bir hata, eve dönüş. Tahminlerini iyi düşün.",
            "prediction_count": 8,
            "deadline": deadline,
        },
        "final": {
            "round_kicker": "Final",
            "round_emoji": "👑",
            "round_title": "Final günü",
            "round_tagline": "Şampiyon kim olacak? Son bir tahmin kaldı.",
            "prediction_count": 1,
            "deadline": deadline,
        },
    }
    return variants[kind]


def _sample_context(slug: str, request=None) -> dict:
    now = timezone.now()
    deadline = now + timedelta(hours=26)
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

    if slug.startswith("round_opened_"):
        kind = slug.removeprefix("round_opened_")
        return {
            **base,
            **_round_opened_variant(kind, now),
            "predict_url": f"{site_url}/predictions/",
        }

    if slug.startswith("round_deadline_"):
        urgency = slug.removeprefix("round_deadline_")
        # Time-until-deadline matches the urgency label for realistic preview
        urgency_to_delta = {
            "24h": timedelta(hours=23, minutes=45),
            "12h": timedelta(hours=11, minutes=30),
            "6h": timedelta(hours=5, minutes=45),
            "30min": timedelta(minutes=28),
        }
        return {
            **base,
            "urgency": urgency,
            "round_name": "Grup aşaması · 1. tur",
            "deadline": now + urgency_to_delta[urgency],
            "pending_count": 4 if urgency != "30min" else 1,
            "predict_url": f"{site_url}/predictions/",
        }

    if slug == "daily_morning":
        def preds(values):
            return [
                {"nickname": n, "prediction": v, "is_self": n == nickname}
                for n, v in zip(_SAMPLE_ROSTER, values)
            ]
        return {
            **base,
            "date": now,
            "matches": [
                {
                    "home": "Meksika", "away": "Kanada",
                    "kickoff": now.replace(hour=18, minute=0),
                    "predictions": preds(["2-1", "1-1", "3-0", "2-0", "1-2", None, "2-1"]),
                },
                {
                    "home": "ABD", "away": "Türkiye",
                    "kickoff": now.replace(hour=21, minute=0),
                    "predictions": preds([None, "0-2", "1-1", "0-1", "2-2", "0-3", "1-2"]),
                },
                {
                    "home": "Suudi Arabistan", "away": "Fas",
                    "kickoff": now.replace(hour=23, minute=30),
                    "predictions": preds(["0-1", "1-2", "0-2", None, "1-1", "0-1", "2-1"]),
                },
            ],
            "has_missing": True,
            "predict_url": f"{site_url}/predictions/",
        }

    if slug == "daily_evening":
        def preds(values_with_scoring):
            # values_with_scoring: list of (prediction, points, chip)
            return [
                {
                    "nickname": n,
                    "prediction": v[0],
                    "points": v[1],
                    "chip": v[2],
                    "is_self": n == nickname,
                }
                for n, v in zip(_SAMPLE_ROSTER, values_with_scoring)
            ]
        return {
            **base,
            "date": now,
            "finished_matches": [
                {
                    "home": "Meksika", "away": "Kanada", "result": "2-1",
                    "predictions": preds([
                        ("2-1", 5, "exact"),
                        ("1-1", 0, "miss"),
                        ("3-0", 0, "miss"),
                        ("2-0", 2, "result"),
                        ("1-2", 0, "miss"),
                        (None, 0, "miss"),
                        ("2-1", 5, "exact"),
                    ]),
                },
                {
                    "home": "ABD", "away": "Türkiye", "result": "0-2",
                    "predictions": preds([
                        (None, 0, "miss"),
                        ("0-2", 5, "exact"),
                        ("1-1", 0, "miss"),
                        ("0-1", 3, "diff"),
                        ("2-2", 0, "miss"),
                        ("0-3", 2, "result"),
                        ("1-2", 3, "diff"),
                    ]),
                },
            ],
            "daily_points": 8,
            "leaderboard": [
                {"rank": 1, "nickname": "Ali", "total": 47, "daily": 5, "is_self": False},
                {"rank": 2, "nickname": "Selin", "total": 44, "daily": 5, "is_self": False},
                {"rank": 3, "nickname": "Hemre", "total": 42, "daily": 8, "is_self": True},
                {"rank": 4, "nickname": "Defne", "total": 39, "daily": 8, "is_self": False},
                {"rank": 5, "nickname": "Zeynep", "total": 35, "daily": 0, "is_self": False},
                {"rank": 6, "nickname": "Emre", "total": 30, "daily": 5, "is_self": False},
                {"rank": 7, "nickname": "Cem", "total": 22, "daily": 2, "is_self": False},
            ],
            "leaderboard_url": f"{site_url}/#leaderboard",
        }

    return base


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
