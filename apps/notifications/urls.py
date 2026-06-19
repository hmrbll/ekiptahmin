from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.email_log_list, name="email_log_list"),
    path("preview/", views.preview_index, name="preview_index"),
    path("preview/<slug:slug>/", views.preview_detail, name="preview_detail"),
]
