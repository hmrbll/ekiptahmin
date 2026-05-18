from django.contrib import admin

from .models import GanyanScore, MatchPool, SlotScore


@admin.register(SlotScore)
class SlotScoreAdmin(admin.ModelAdmin):
    """LEGACY scoring cache. Read-only listing for staff investigation."""

    list_display = (
        "user", "slot", "total", "matchup_type",
        "earning_round_order", "updated_at",
    )
    list_filter = ("matchup_type", "slot__stage")
    search_fields = ("user__email", "user__nickname", "slot__position")
    raw_id_fields = ("user", "slot")
    readonly_fields = ("updated_at",)


@admin.register(GanyanScore)
class GanyanScoreAdmin(admin.ModelAdmin):
    """Active parimutuel score cache. One row per (user, slot)."""

    list_display = (
        "user", "slot", "total",
        "score_exact", "score_diff", "score_result", "score_penalty",
        "outcome", "effective_round", "wrong_count_contribution", "updated_at",
    )
    list_filter = ("outcome", "slot__stage", "effective_round")
    search_fields = ("user__email", "user__nickname", "slot__position")
    raw_id_fields = ("user", "slot", "effective_round")
    readonly_fields = ("updated_at",)


@admin.register(MatchPool)
class MatchPoolAdmin(admin.ModelAdmin):
    """Per (slot, criterion) ganyan pool snapshot. Powers the tablosu UI."""

    list_display = (
        "slot", "criterion", "pool_size",
        "predictor_count", "winner_count", "base_payout", "computed_at",
    )
    list_filter = ("criterion", "slot__stage")
    search_fields = ("slot__position",)
    raw_id_fields = ("slot",)
    readonly_fields = ("computed_at",)
