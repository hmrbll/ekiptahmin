from django.contrib import admin

from .models import SlotScore


@admin.register(SlotScore)
class SlotScoreAdmin(admin.ModelAdmin):
    list_display = (
        "user", "slot", "total", "matchup_type",
        "earning_round_order", "updated_at",
    )
    list_filter = ("matchup_type", "slot__stage")
    search_fields = ("user__email", "user__nickname", "slot__position")
    raw_id_fields = ("user", "slot")
    readonly_fields = ("updated_at",)
