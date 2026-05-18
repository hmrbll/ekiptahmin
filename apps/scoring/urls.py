from django.urls import path

from . import views

urlpatterns = [
    path("leaderboard/", views.leaderboard, name="leaderboard"),
    path("leaderboard/<int:user_id>/", views.user_detail, name="leaderboard_user_detail"),
    path("results/", views.results_list, name="results"),
    path("matches/<int:slot_id>/", views.match_detail, name="match_detail"),
]
