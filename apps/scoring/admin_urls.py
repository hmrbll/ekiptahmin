"""URL patterns for the staff result-entry wizard.

Included before Django admin in config/urls.py so `/admin/results/...`
resolves here rather than to the django.contrib.admin tree.
"""

from django.urls import path

from . import admin_views as views

urlpatterns = [
    path("", views.admin_results_entry, name="admin_results_entry"),
    path("group/<str:letter>/", views.admin_results_group_step, name="admin_results_group"),
    path("groups/summary/", views.admin_results_groups_summary, name="admin_results_groups_summary"),
    path("knockout/<str:kind>/", views.admin_results_knockout_step, name="admin_results_knockout"),
    path("knockout/summary/", views.admin_results_knockout_summary, name="admin_results_knockout_summary"),
    path("save/<int:slot_id>/", views.admin_results_save, name="admin_results_save"),
]
