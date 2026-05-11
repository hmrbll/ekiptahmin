from django.urls import path

from . import views

urlpatterns = [
    path("", views.leaderboard, name="leaderboard"),
    path("<int:user_id>/", views.user_detail, name="leaderboard_user_detail"),
]
