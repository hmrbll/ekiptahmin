from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class Tournament(models.Model):
    """A single tournament container (e.g., FIFA World Cup 2026).

    Designed for multi-tournament reuse: each tournament has its own teams,
    stages, prediction rounds, and bracket slots.
    """

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(
        default=True,
        help_text="Only one active tournament should exist at a time (drives default UI selections).",
    )
    timezone = models.CharField(
        max_length=64,
        default="Europe/Istanbul",
        help_text="Default display TZ for anonymous visitors. Authenticated users use their own.",
    )

    class Meta:
        ordering = ("-start_date",)

    def __str__(self) -> str:
        return self.name


class Stage(models.Model):
    """A stage of the tournament (Group, R32, R16, QF, SF, Third Place, Final).

    Scoring parameters live here — admin-tunable, never hardcoded in Python.
    Different tournaments can have different stage layouts (2022 had no R32).
    """

    GROUP = "GROUP"
    R32 = "R32"
    R16 = "R16"
    QF = "QF"
    SF = "SF"
    THIRD = "THIRD"
    FINAL = "FINAL"
    KIND_CHOICES = [
        (GROUP, "Group Stage"),
        (R32, "Round of 32"),
        (R16, "Round of 16"),
        (QF, "Quarter Final"),
        (SF, "Semi Final"),
        (THIRD, "Third Place Match"),
        (FINAL, "Final"),
    ]

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="stages")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    order = models.PositiveSmallIntegerField(help_text="0 = Group, 6 = Final (defines progression)")

    points_exact = models.PositiveSmallIntegerField(
        help_text="Points awarded when the predicted score exactly matches the actual score.",
    )
    points_diff = models.PositiveSmallIntegerField(
        help_text="Points for correct outcome AND correct goal difference (but wrong exact score).",
    )
    points_result = models.PositiveSmallIntegerField(
        help_text="Points for correct outcome only (winner or draw).",
    )
    penalty_loser_pct = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal("0.60"),
        help_text="When a user did NOT predict a draw but correctly named the team that "
                  "advanced through penalties: percentage of `points_result` they receive "
                  "(rounded to nearest integer, then multiplied by round weight).",
    )

    class Meta:
        ordering = ("tournament", "order")
        unique_together = (("tournament", "kind"),)

    def __str__(self) -> str:
        return f"{self.get_kind_display()} ({self.tournament.slug})"


class Team(models.Model):
    """A national team participating in a specific tournament.

    Teams are tournament-scoped because group assignments and participant
    rosters change between tournaments.
    """

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="teams")
    code = models.CharField(max_length=3, help_text="3-letter code (TUR, BRA, ESP).")
    name_tr = models.CharField(max_length=100, help_text="Country name in Turkish (user-facing).")
    flag_emoji = models.CharField(
        max_length=8,
        blank=True,
        help_text="Flag emoji 🇹🇷 (fallback display until SVG flags are added).",
    )
    group_letter = models.CharField(
        max_length=1,
        blank=True,
        help_text="A-L for the 12 groups in 2026. Empty for teams that didn't reach group stage.",
    )

    class Meta:
        ordering = ("group_letter", "name_tr")
        unique_together = (("tournament", "code"),)

    def __str__(self) -> str:
        return f"{self.flag_emoji} {self.name_tr}".strip()


class PredictionRound(models.Model):
    """One of the prediction rounds (pre-tournament, after group stage, etc.).

    The `weight` field is the multiplier applied to scores from predictions made
    in this round. Earlier rounds carry higher weight to reward bold/early calls.
    """

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="prediction_rounds")
    name = models.CharField(max_length=100, help_text="e.g., 'Pre-tournament', 'After Group Stage'.")
    order = models.PositiveSmallIntegerField()
    deadline = models.DateTimeField(help_text="UTC; predictions for this round can no longer be edited after this.")
    weight = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        help_text="Score multiplier (1.00 = full points, 0.50 = half points).",
    )
    editable_stages = models.ManyToManyField(
        Stage,
        related_name="editable_in_rounds",
        help_text="Which stages can be predicted/edited in this round.",
    )

    class Meta:
        ordering = ("tournament", "order")
        unique_together = (("tournament", "order"),)

    def __str__(self) -> str:
        return f"{self.name} (×{self.weight})"

    @property
    def is_open(self) -> bool:
        return timezone.now() < self.deadline


class BracketSlot(models.Model):
    """A position in the tournament bracket, identified by a stable string ID.

    Position naming examples:
    - Group: 'GroupA-M1' .. 'GroupL-M6'
    - Knockout: 'R32-1' .. 'R32-16', 'R16-1' .. 'R16-8',
      'QF-1' .. 'QF-4', 'SF-1', 'SF-2', 'Third', 'Final'
    """

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="slots")
    stage = models.ForeignKey(Stage, on_delete=models.PROTECT, related_name="slots")
    position = models.CharField(
        max_length=30,
        db_index=True,
        help_text="Stable ID, e.g., 'GroupA-M1', 'R32-1', 'QF-3', 'Final', 'Third'.",
    )

    scheduled_kickoff = models.DateTimeField(help_text="UTC; rendered in user's TZ.")
    venue = models.CharField(max_length=100, blank=True)

    # Group matches: teams known at seed time. Knockout: filled in as bracket resolves.
    home_team_actual = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="home_slots",
    )
    away_team_actual = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="away_slots",
    )

    # Free-text source description, used for knockout slots until teams are determined
    # (e.g., 'Group A 1st', 'Winner of R32-1'). Useful for the bracket display.
    home_source = models.CharField(
        max_length=80,
        blank=True,
        help_text="Source description for unresolved slots, e.g., 'Group A 1st' or 'Winner of R32-1'.",
    )
    away_source = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ("scheduled_kickoff",)
        unique_together = (("tournament", "position"),)

    def __str__(self) -> str:
        return f"{self.position} — {self.stage.get_kind_display()}"

    @property
    def is_locked(self) -> bool:
        """Predictions for this slot are locked once kickoff has passed."""
        return timezone.now() >= self.scheduled_kickoff


class ActualResult(models.Model):
    """The actual result for a slot, entered by an admin.

    The 90-minute score is the canonical scoring basis (matches the 2022 group rules).
    Extra time and penalty shootouts are tracked via separate flags so the scoring
    engine can apply the special-case rules from `Stage.penalty_loser_pct`.
    """

    slot = models.OneToOneField(BracketSlot, on_delete=models.CASCADE, related_name="result")
    home_score = models.PositiveSmallIntegerField(help_text="Score at the end of regulation (90').")
    away_score = models.PositiveSmallIntegerField(help_text="Score at the end of regulation (90').")

    went_to_extra_time = models.BooleanField(default=False)
    went_to_penalties = models.BooleanField(default=False)

    # Penalty details (only populated when went_to_penalties=True)
    penalty_winner = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="penalty_wins",
    )
    home_penalties = models.PositiveSmallIntegerField(null=True, blank=True)
    away_penalties = models.PositiveSmallIntegerField(null=True, blank=True)

    entered_at = models.DateTimeField(auto_now=True)
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    def __str__(self) -> str:
        score = f"{self.home_score}-{self.away_score}"
        if self.went_to_penalties and self.penalty_winner:
            score += f" (pen: {self.penalty_winner.code})"
        return f"{self.slot.position}: {score}"
