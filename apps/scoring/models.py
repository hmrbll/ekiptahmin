"""Materialized per-(user, slot) score rows.

The pure-Python engine in apps/scoring/engine.py computes the breakdown;
this model caches its output so the leaderboard view can sum points without
re-walking every user's predictions on every page load.

Cache invalidation lives in apps/scoring/signals.py — ActualResult and
SlotPrediction writes both trigger a recompute of the affected (user, slot)
rows. A row with `matchup_type = "no_result"` means the slot has no actual
result yet (no points awarded, but we still keep the row so the leaderboard
query can join cleanly).
"""

from django.conf import settings
from django.db import models


class SlotScore(models.Model):
    """One materialized score row per (user, slot)."""

    EXACT = "exact"
    DIFF = "diff"
    RESULT = "result"
    PENALTY_LOSER_BONUS = "penalty_loser_bonus"
    MISS = "miss"
    NO_PREDICTION = "no_prediction"
    NO_RESULT = "no_result"
    MATCHUP_TYPE_CHOICES = [
        (EXACT, "Exact score"),
        (DIFF, "Correct goal difference"),
        (RESULT, "Correct outcome"),
        (PENALTY_LOSER_BONUS, "Penalty loser bonus"),
        (MISS, "Wrong / no points"),
        (NO_PREDICTION, "No prediction"),
        (NO_RESULT, "Actual result not entered"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="slot_scores",
    )
    slot = models.ForeignKey(
        "tournament.BracketSlot",
        on_delete=models.CASCADE,
        related_name="user_scores",
    )

    points_match = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    points_penalty = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    matchup_type = models.CharField(
        max_length=24,
        choices=MATCHUP_TYPE_CHOICES,
        default=NO_PREDICTION,
    )
    earning_round_order = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Order of the prediction round whose prediction earned these points. "
                  "Null when no points were earned (miss / no prediction / no result).",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "slot"),)
        indexes = [
            models.Index(fields=("user",)),
            models.Index(fields=("slot",)),
            models.Index(fields=("earning_round_order",)),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} | {self.slot.position} | {self.total} ({self.matchup_type})"
