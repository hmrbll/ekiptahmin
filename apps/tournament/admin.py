from django.contrib import admin

from .models import ActualResult, BracketSlot, PredictionRound, Stage, Team, Tournament


class StageInline(admin.TabularInline):
    model = Stage
    extra = 0
    fields = ("order", "kind", "points_exact", "points_diff", "points_result", "penalty_loser_pct")
    ordering = ("order",)


class PredictionRoundInline(admin.TabularInline):
    model = PredictionRound
    extra = 0
    fields = ("order", "name", "deadline", "weight")
    ordering = ("order",)


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "start_date", "end_date", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [StageInline, PredictionRoundInline]


@admin.register(Stage)
class StageAdmin(admin.ModelAdmin):
    list_display = ("tournament", "order", "kind", "points_exact", "points_diff", "points_result", "penalty_loser_pct")
    list_filter = ("tournament", "kind")
    list_editable = ("points_exact", "points_diff", "points_result", "penalty_loser_pct")
    ordering = ("tournament", "order")


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("flag_emoji", "name_tr", "code", "group_letter", "tournament")
    list_filter = ("tournament", "group_letter")
    search_fields = ("name_tr", "code")
    list_display_links = ("name_tr",)
    ordering = ("group_letter", "name_tr")


@admin.register(PredictionRound)
class PredictionRoundAdmin(admin.ModelAdmin):
    list_display = ("name", "tournament", "order", "deadline", "weight", "is_open")
    list_filter = ("tournament",)
    filter_horizontal = ("editable_stages",)
    ordering = ("tournament", "order")

    @admin.display(boolean=True, description="Open?")
    def is_open(self, obj):
        return obj.is_open


class ActualResultInline(admin.StackedInline):
    model = ActualResult
    extra = 0
    fields = (
        "home_score",
        "away_score",
        "went_to_extra_time",
        "went_to_penalties",
        "penalty_winner",
        "home_penalties",
        "away_penalties",
    )


@admin.register(BracketSlot)
class BracketSlotAdmin(admin.ModelAdmin):
    list_display = (
        "position",
        "stage",
        "scheduled_kickoff",
        "home_team_actual",
        "away_team_actual",
        "venue",
        "is_locked",
    )
    list_filter = ("tournament", "stage")
    search_fields = ("position", "venue", "home_source", "away_source")
    ordering = ("scheduled_kickoff",)
    raw_id_fields = ("home_team_actual", "away_team_actual")
    inlines = [ActualResultInline]
    fieldsets = (
        (None, {"fields": ("tournament", "stage", "position")}),
        ("Schedule", {"fields": ("scheduled_kickoff", "venue")}),
        ("Teams", {"fields": ("home_team_actual", "away_team_actual", "home_source", "away_source")}),
    )

    @admin.display(boolean=True, description="Locked?")
    def is_locked(self, obj):
        return obj.is_locked


@admin.register(ActualResult)
class ActualResultAdmin(admin.ModelAdmin):
    list_display = ("slot", "home_score", "away_score", "went_to_penalties", "penalty_winner", "entered_at", "entered_by")
    list_filter = ("went_to_penalties", "went_to_extra_time", "slot__stage")
    search_fields = ("slot__position",)
    raw_id_fields = ("slot", "penalty_winner", "entered_by")
    readonly_fields = ("entered_at",)

    def save_model(self, request, obj, form, change):
        if not obj.entered_by:
            obj.entered_by = request.user
        super().save_model(request, obj, form, change)
