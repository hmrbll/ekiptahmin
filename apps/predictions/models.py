"""SlotPrediction — one user's prediction for one slot in one prediction round.

Design notes:
- (user, prediction_round, slot) is the natural key. Editing a prediction
  overwrites the same row; we never store history within a single round.
- `home_team`/`away_team` are stored on every prediction (even for group slots
  where the teams are fixed) so the scoring engine sees a uniform shape and
  doesn't need to special-case stages.
- Time-based locks (round deadline, slot kickoff) are NOT enforced in `clean()`
  because model validation doesn't know whether the caller is an end-user or
  a staff member fixing data. The view/form layer is the gate for users.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models

# Sanity cap on score inputs — no real match has gone above this in regulation.
MAX_GOALS = 20
MAX_PENALTY_KICKS = 30


class SlotPrediction(models.Model):
    """A user's prediction for one bracket slot in one prediction round."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="slot_predictions",
    )
    prediction_round = models.ForeignKey(
        "tournament.PredictionRound",
        on_delete=models.CASCADE,
        related_name="slot_predictions",
    )
    slot = models.ForeignKey(
        "tournament.BracketSlot",
        on_delete=models.CASCADE,
        related_name="predictions",
    )

    home_team = models.ForeignKey(
        "tournament.Team",
        on_delete=models.PROTECT,
        related_name="predicted_as_home",
        help_text="For group slots: must match slot.home_team_actual. "
                  "For knockout slots: user's bracket forecast.",
    )
    away_team = models.ForeignKey(
        "tournament.Team",
        on_delete=models.PROTECT,
        related_name="predicted_as_away",
    )

    home_score = models.PositiveSmallIntegerField(validators=[MaxValueValidator(MAX_GOALS)])
    away_score = models.PositiveSmallIntegerField(validators=[MaxValueValidator(MAX_GOALS)])

    # Only meaningful when the user predicted a draw on a knockout slot
    # (knockout matches must be decided — penalties are the tiebreaker).
    penalty_winner = models.ForeignKey(
        "tournament.Team",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="predicted_penalty_wins",
    )
    home_penalties = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MaxValueValidator(MAX_PENALTY_KICKS)],
    )
    away_penalties = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MaxValueValidator(MAX_PENALTY_KICKS)],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "prediction_round", "slot"),)
        indexes = [
            models.Index(fields=("user", "prediction_round")),
            models.Index(fields=("slot",)),
        ]
        ordering = ("user", "prediction_round", "slot")

    def __str__(self) -> str:
        return (
            f"{self.user_id} | R{self.prediction_round_id} | {self.slot.position} "
            f"| {self.home_team.code} {self.home_score}-{self.away_score} {self.away_team.code}"
        )

    def is_draw_prediction(self) -> bool:
        return self.home_score == self.away_score

    def clean(self) -> None:
        errors: dict[str, str] = {}

        # Same tournament across slot, round, teams.
        if self.slot_id and self.prediction_round_id:
            if self.slot.tournament_id != self.prediction_round.tournament_id:
                errors["slot"] = "Slot ve prediction round farklı turnuvalardan."

        if self.slot_id and self.home_team_id:
            if self.home_team.tournament_id != self.slot.tournament_id:
                errors["home_team"] = "Ev sahibi takımı slot'un turnuvasında değil."
        if self.slot_id and self.away_team_id:
            if self.away_team.tournament_id != self.slot.tournament_id:
                errors["away_team"] = "Deplasman takımı slot'un turnuvasında değil."

        # Slot stage must be editable in this round.
        if self.slot_id and self.prediction_round_id:
            editable_ids = set(
                self.prediction_round.editable_stages.values_list("id", flat=True)
            )
            if self.slot.stage_id not in editable_ids:
                errors["slot"] = (
                    "Bu slot'un aşaması seçili round'da düzenlenebilir değil."
                )

        # Same team can't play itself.
        if self.home_team_id and self.away_team_id and self.home_team_id == self.away_team_id:
            errors["away_team"] = "Ev ve deplasman takımı aynı olamaz."

        # For group slots the teams are fixed — predictions can't override them.
        if self.slot_id:
            slot = self.slot
            if slot.home_team_actual_id and self.home_team_id != slot.home_team_actual_id:
                errors["home_team"] = (
                    "Grup maçında ev sahibi takım değiştirilemez."
                )
            if slot.away_team_actual_id and self.away_team_id != slot.away_team_actual_id:
                errors["away_team"] = (
                    "Grup maçında deplasman takımı değiştirilemez."
                )

        # Penalty fields: required iff draw on knockout, forbidden otherwise.
        is_knockout = self.slot_id and self.slot.stage.kind != "GROUP"
        draw = self.home_score is not None and self.away_score is not None and self.is_draw_prediction()

        if is_knockout and draw:
            if not self.penalty_winner_id:
                errors["penalty_winner"] = (
                    "Knockout berabere tahmininde penaltı kazananı belirtilmeli."
                )
            elif self.home_team_id and self.away_team_id and self.penalty_winner_id not in {
                self.home_team_id, self.away_team_id,
            }:
                errors["penalty_winner"] = (
                    "Penaltı kazananı maçtaki iki takımdan biri olmalı."
                )
            if self.home_penalties is None or self.away_penalties is None:
                errors.setdefault(
                    "home_penalties",
                    "Penaltı skoru girilmeli (her iki takım için).",
                )
            elif self.home_penalties == self.away_penalties:
                errors["away_penalties"] = "Penaltılar berabere bitemez."
        else:
            if self.penalty_winner_id or self.home_penalties is not None or self.away_penalties is not None:
                errors["penalty_winner"] = (
                    "Penaltı alanları sadece knockout'ta berabere tahmininde doldurulur."
                )

        if errors:
            raise ValidationError(errors)
