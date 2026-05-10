"""Forms for end-user prediction submissions.

Time-based locks (round deadline + slot kickoff) live here, NOT in the
SlotPrediction model. The model accepts staff fixes after lock; the form
is the user-facing gate.

Cascade rule (knockout R16 and beyond):
- BracketSlot has FK links (home_source_slot, away_source_slot) to the
  prior-round slots whose winner/loser feeds each side.
- When the user opens the form for a cascaded slot, we look up THEIR own
  prediction for the source slots in any round and derive the team.
- Team fields render disabled, locked to the derived team. Score fields
  remain editable.
- If the user hasn't predicted the source slot yet, the form is blocked
  with a `cascade_blocked` flag — the view shows a "predict X first" page.
"""

from django import forms
from django.core.exceptions import ValidationError

from apps.tournament.models import BracketSlot, Team

from .models import SlotPrediction


def _derive_cascaded_team(user, source_slot: BracketSlot, source_kind: str):
    """Look up the user's latest prediction for `source_slot` and return the
    winner or loser team based on `source_kind`. Returns None if no
    prediction exists or the prediction has no determinate winner (draw
    without penalty winner).
    """
    if source_slot is None:
        return None
    pred = (
        SlotPrediction.objects
        .filter(user=user, slot=source_slot)
        .select_related("home_team", "away_team", "penalty_winner")
        .order_by("-prediction_round__order")
        .first()
    )
    if pred is None:
        return None
    return pred.winner_team() if source_kind == BracketSlot.SOURCE_KIND_WINNER else pred.loser_team()


class SlotPredictionForm(forms.ModelForm):
    """Form for one user submitting a prediction for one slot in one round."""

    class Meta:
        model = SlotPrediction
        fields = [
            "home_team", "away_team",
            "home_score", "away_score",
            "penalty_winner", "home_penalties", "away_penalties",
        ]

    def __init__(self, *args, user, prediction_round, slot, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.prediction_round = prediction_round
        self.slot = slot
        self.cascade_blocked_on: list[BracketSlot] = []

        teams_qs = Team.objects.filter(tournament=slot.tournament).order_by("name_tr")
        self.fields["home_team"].queryset = teams_qs
        self.fields["away_team"].queryset = teams_qs
        self.fields["penalty_winner"].queryset = teams_qs
        self.fields["penalty_winner"].required = False

        # Group slots: teams are fixed, render as disabled (initial fills them in).
        if slot.home_team_actual_id and slot.away_team_actual_id:
            self.fields["home_team"].initial = slot.home_team_actual
            self.fields["away_team"].initial = slot.away_team_actual
            self.fields["home_team"].disabled = True
            self.fields["away_team"].disabled = True
            return

        # Cascaded knockout slots: derive teams from the user's prior predictions.
        if slot.home_source_slot_id:
            home_team = _derive_cascaded_team(user, slot.home_source_slot, slot.home_source_kind)
            if home_team is None:
                self.cascade_blocked_on.append(slot.home_source_slot)
            else:
                self.fields["home_team"].initial = home_team
                self.fields["home_team"].disabled = True

        if slot.away_source_slot_id:
            away_team = _derive_cascaded_team(user, slot.away_source_slot, slot.away_source_kind)
            if away_team is None:
                self.cascade_blocked_on.append(slot.away_source_slot)
            else:
                self.fields["away_team"].initial = away_team
                self.fields["away_team"].disabled = True

    def clean(self):
        cleaned = super().clean()
        if self.cascade_blocked_on:
            raise ValidationError(
                "Bu slot için takımlar önceki round'lardan türetilir — önce bağlı maçları tahmin et."
            )
        if not self.prediction_round.is_open:
            raise ValidationError("Bu round'un süresi doldu — tahmin gönderilemez.")
        if self.slot.is_locked:
            raise ValidationError("Bu maçın kickoff'u geçti — tahmin gönderilemez.")
        return cleaned

    def save(self, commit=True):
        instance: SlotPrediction = super().save(commit=False)
        instance.user = self.user
        instance.prediction_round = self.prediction_round
        instance.slot = self.slot
        if commit:
            instance.full_clean()
            instance.save()
        return instance
