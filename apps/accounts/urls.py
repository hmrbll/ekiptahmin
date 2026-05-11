from django.urls import path

from . import views

urlpatterns = [
    path("invite/<str:code>/", views.invite_signup, name="invite_signup"),
    path("auth/login/", views.login_request, name="login"),
    path("auth/confirm/", views.confirm_token, name="confirm_token"),
    path("auth/logout/", views.logout_view, name="logout"),
]
