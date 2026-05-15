from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("preview/", views.preview_index, name="preview_index"),
    path("preview/<slug:slug>/", views.preview_detail, name="preview_detail"),
]
