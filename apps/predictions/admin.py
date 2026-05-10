from django.contrib import admin

from .models import SlotPrediction


@admin.register(SlotPrediction)
class SlotPredictionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "prediction_round",
        "slot",
        "home_team",
        "home_score",
        "away_score",
        "away_team",
        "penalty_winner",
        "updated_at",
    )
    list_filter = ("prediction_round", "slot__stage")
    search_fields = ("user__email", "user__nickname", "slot__position")
    raw_id_fields = ("user", "slot", "home_team", "away_team", "penalty_winner")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("user", "prediction_round", "slot")}),
        ("Prediction", {"fields": ("home_team", "away_team", "home_score", "away_score")}),
        ("Penalties", {"fields": ("penalty_winner", "home_penalties", "away_penalties")}),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )
