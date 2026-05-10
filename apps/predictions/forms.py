"""Forms for end-user prediction submissions.

Time-based locks (round deadline + slot kickoff) live here, NOT in the
SlotPrediction model. The model accepts staff fixes after lock; the form
is the user-facing gate.
"""

from django import forms
from django.core.exceptions import ValidationError

from apps.tournament.models import Team

from .models import SlotPrediction


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

    def clean(self):
        cleaned = super().clean()
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
