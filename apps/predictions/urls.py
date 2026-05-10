from django.urls import path

from . import views

urlpatterns = [
    path("", views.prediction_rounds, name="prediction_rounds"),
    path(
        "round/<int:round_id>/",
        views.prediction_round_detail,
        name="prediction_round_detail",
    ),
    path(
        "round/<int:round_id>/slot/<int:slot_id>/save/",
        views.slot_prediction_save,
        name="slot_prediction_save",
    ),
]
