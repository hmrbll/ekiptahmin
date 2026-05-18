"""Staff-only legacy bracket-scoring URLs.

Mounted under /legacy/ from config/urls.py. Public site uses the ganyan
engine (see urls.py + views_public.py).
"""

from django.contrib.admin.views.decorators import staff_member_required
from django.urls import path

from . import legacy_views

urlpatterns = [
    path(
        "leaderboard/",
        staff_member_required(legacy_views.leaderboard),
        name="legacy_leaderboard",
    ),
    path(
        "leaderboard/<int:user_id>/",
        staff_member_required(legacy_views.user_detail),
        name="legacy_user_detail",
    ),
    path(
        "results/",
        staff_member_required(legacy_views.results_list),
        name="legacy_results",
    ),
    path(
        "scoring-diff/",
        staff_member_required(legacy_views.scoring_diff),
        name="legacy_scoring_diff",
    ),
]
