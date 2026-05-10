from django.urls import path

from . import views

urlpatterns = [
    path("", views.prediction_rounds, name="prediction_rounds"),

    # Wizard entry — redirects to the first step of the round
    path(
        "round/<int:round_id>/",
        views.predict_round_entry,
        name="predict_round_entry",
    ),
    # Legacy alias kept so old templates/bookmarks keep resolving
    path(
        "round/<int:round_id>/",
        views.predict_round_entry,
        name="prediction_round_detail",
    ),
    path(
        "round/<int:round_id>/group/<str:letter>/",
        views.predict_group_step,
        name="predict_group_step",
    ),
    path(
        "round/<int:round_id>/groups-summary/",
        views.predict_groups_summary,
        name="predict_groups_summary",
    ),
    path(
        "round/<int:round_id>/knockout/",
        views.predict_knockout_step,
        name="predict_knockout_step",
    ),

    # POST endpoint (HTMX or fallback) for saving one slot's prediction
    path(
        "round/<int:round_id>/slot/<int:slot_id>/save/",
        views.slot_prediction_save,
        name="slot_prediction_save",
    ),
]
