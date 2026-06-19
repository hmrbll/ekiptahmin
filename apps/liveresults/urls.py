from django.urls import path

from . import views

urlpatterns = [
    path("live/", views.live_scores, name="live_scores"),
]
