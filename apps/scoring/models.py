"""Materialized score caches for the two scoring engines.

`SlotScore`  → legacy bracket engine (`apps/scoring/engine.py`). Stays alive
               for staff-only /legacy/* views; not used by the public site.

`GanyanScore` + `MatchPool` → active parimutuel engine (`apps/scoring/ganyan.py`).
                              Computed alongside SlotScore on `ActualResult` save
                              via `apps/scoring/signals.py`.

See docs/scoring-ganyan.md for the formula and design rationale.
"""

from django.conf import settings
from django.db import models


class SlotScore(models.Model):
    """LEGACY: one materialized score row per (user, slot), bracket engine."""

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


class GanyanScore(models.Model):
    """Parimutuel score per (user, slot). Active engine — drives public leaderboard.

    `score` = sum of payouts from each criterion the user satisfied in their
    effective round, multiplied by that round's weight. The effective round is
    the one maximizing the user's weighted total across all of their rounds
    for this slot — same single-round-per-(user, slot) semantics as the legacy
    engine, but with payouts coming from shared pools rather than fixed values.
    """

    # Outcome of the user-vs-slot pairing — drives UI labelling. The three
    # penalty criteria collapse to one PENALTY tier for this headline badge.
    EXACT = "exact"
    DIFF = "diff"
    RESULT = "result"
    PENALTY = "penalty"
    MISS = "miss"                  # predicted, scored zero
    NO_PREDICTION = "no_prediction"
    NO_RESULT = "no_result"
    OUTCOME_CHOICES = [
        (EXACT, "Exact score"),
        (DIFF, "Correct goal difference"),
        (RESULT, "Correct outcome"),
        (PENALTY, "Penalty shootout only (KO)"),
        (MISS, "Wrong / no points"),
        (NO_PREDICTION, "No prediction"),
        (NO_RESULT, "Actual result not entered"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ganyan_scores",
    )
    slot = models.ForeignKey(
        "tournament.BracketSlot",
        on_delete=models.CASCADE,
        related_name="ganyan_scores",
    )

    # Per-criterion payouts (already weighted by effective round weight).
    # score_penalty is the combined payout from the three penalty criteria
    # (winner + shootout score + shootout diff); the per-criterion split lives
    # in MatchPool for the match-detail ganyan tablosu.
    score_exact = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    score_diff = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    score_result = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    score_penalty = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # The single round the user "earns from" for this slot — the one whose
    # weighted criterion sum was highest. Null when user didn't predict or
    # nothing scored.
    effective_round = models.ForeignKey(
        "tournament.PredictionRound",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="ganyan_scores",
    )

    # Best matchup-type the user achieved (highest tier among criteria they
    # satisfied). Drives UI badges + tiebreaker counts.
    outcome = models.CharField(
        max_length=16,
        choices=OUTCOME_CHOICES,
        default=NO_PREDICTION,
    )

    # Tiebreaker 5 contribution: 1 if user predicted this slot and scored 0,
    # else 0. Summed across slots for the "az yanlış" tiebreaker.
    wrong_count_contribution = models.PositiveSmallIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "slot"),)
        indexes = [
            models.Index(fields=("user",)),
            models.Index(fields=("slot",)),
            models.Index(fields=("outcome",)),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} | {self.slot.position} | {self.total} ({self.outcome})"


class MatchPool(models.Model):
    """Per (slot, criterion) pool snapshot. Drives the public ganyan tablosu UI.

    Rebuilt from scratch whenever a slot's ActualResult is written. Pre-result
    pools (just predictor counts, no winners) are also computed at lock time
    so the tablosu can show what each prediction would pay before the match ends.
    """

    EXACT = "exact"
    DIFF = "diff"
    RESULT = "result"
    PENALTY_WINNER = "penalty_winner"
    PENALTY_SCORE = "penalty_score"
    PENALTY_DIFF = "penalty_diff"
    ADVANCER = "advancer"
    CRITERION_CHOICES = [
        (EXACT, "Exact score"),
        (DIFF, "Goal difference"),
        (RESULT, "Outcome (1X2)"),
        (PENALTY_WINNER, "Penalty shootout winner (shootout predictions only)"),
        (PENALTY_SCORE, "Penalty shootout exact score"),
        (PENALTY_DIFF, "Penalty shootout difference"),
        (ADVANCER, "Advancing team (open to all, pens matches only)"),
    ]

    slot = models.ForeignKey(
        "tournament.BracketSlot",
        on_delete=models.CASCADE,
        related_name="pools",
    )
    criterion = models.CharField(max_length=16, choices=CRITERION_CHOICES)

    pool_size = models.PositiveIntegerField(
        help_text="Snapshot of Stage.pool_<criterion> at compute time.",
    )
    predictor_count = models.PositiveIntegerField(
        default=0,
        help_text="Users whose effective pick is on the actual fixture (= N). "
                  "Wrong-matchup picks (a different bracket) are excluded.",
    )
    winner_count = models.PositiveIntegerField(
        default=0,
        help_text="Users whose at-least-one round prediction satisfied this criterion.",
    )
    base_payout = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True,
        help_text="pool_size / winner_count, before round weight. Null when winner_count = 0 (pool burned).",
    )

    # JSON shape: {"1-0": 7, "2-1": 3, "2-0": 2} for EXACT;
    # {"1": 12, "2": 3, "0": 1} for DIFF (signed diff: home - away);
    # {"H": 9, "A": 4, "D": 1} for RESULT;
    # {"BRA": 5, "ARG": 3} for PENALTY_WINNER;
    # {"4-3": 2, "5-4": 1} for PENALTY_SCORE; {"1": 3} for PENALTY_DIFF.
    breakdown = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-prediction-value counts for the ganyan tablosu UI.",
    )

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("slot", "criterion"),)
        indexes = [
            models.Index(fields=("slot",)),
        ]

    def __str__(self) -> str:
        n = self.winner_count
        return f"{self.slot.position} | {self.criterion} | {self.pool_size}/{n}"

    @property
    def is_burned(self) -> bool:
        return self.winner_count == 0
