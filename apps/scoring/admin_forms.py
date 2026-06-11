"""Forms used by the staff result-entry wizard.

Two parallel ModelForms cover one slot row:
- `SlotTeamsForm` — only relevant for knockout slots where the bracket
  resolution hasn't been filled in yet; admin picks home/away teams from
  the tournament's full team list.
- `ActualResultForm` — the score and penalty fields on ActualResult.

The wizard view orchestrates both; group slots only need the result form.
"""

from django import forms
from django.core.exceptions import ValidationError

from apps.tournament.models import ActualResult, BracketSlot, Team


class SlotTeamsForm(forms.ModelForm):
    """Pick home/away team for a knockout slot that hasn't resolved yet."""

    class Meta:
        model = BracketSlot
        fields = ["home_team_actual", "away_team_actual"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Both team queryset scoped to this slot's tournament.
        tournament = self.instance.tournament if self.instance.pk else None
        if tournament is not None:
            qs = Team.objects.filter(tournament=tournament).order_by("name_tr")
            self.fields["home_team_actual"].queryset = qs
            self.fields["away_team_actual"].queryset = qs

    def clean(self):
        cleaned = super().clean()
        h = cleaned.get("home_team_actual")
        a = cleaned.get("away_team_actual")
        if h and a and h.id == a.id:
            # English: this form renders only on the /admin/results/ wizard.
            raise ValidationError("Home and away cannot be the same team.")
        return cleaned


class ActualResultForm(forms.ModelForm):
    """Score + extra-time / penalty fields on the slot's ActualResult."""

    class Meta:
        model = ActualResult
        fields = [
            "home_score", "away_score",
            "went_to_extra_time", "went_to_penalties",
            "penalty_winner", "home_penalties", "away_penalties",
        ]

    def __init__(self, *args, slot: BracketSlot, **kwargs):
        super().__init__(*args, **kwargs)
        self.slot = slot
        # penalty_winner is one of the two teams on this slot when teams are set.
        if slot.home_team_actual_id and slot.away_team_actual_id:
            self.fields["penalty_winner"].queryset = Team.objects.filter(
                id__in=[slot.home_team_actual_id, slot.away_team_actual_id],
            )
        else:
            self.fields["penalty_winner"].queryset = Team.objects.none()
        self.fields["penalty_winner"].required = False
        self.fields["home_penalties"].required = False
        self.fields["away_penalties"].required = False

    def clean(self):
        cleaned = super().clean()
        home = cleaned.get("home_score")
        away = cleaned.get("away_score")
        went_pen = cleaned.get("went_to_penalties")
        pen_winner = cleaned.get("penalty_winner")
        h_pen = cleaned.get("home_penalties")
        a_pen = cleaned.get("away_penalties")

        if went_pen:
            # English: this form renders only on the /admin/results/ wizard.
            if home != away:
                raise ValidationError(
                    "A match that went to penalties must have a drawn 90' score."
                )
            if not pen_winner:
                raise ValidationError("Penalty shootout winner is required.")
            if h_pen is None or a_pen is None:
                raise ValidationError("Penalty score is required for both sides.")
            if h_pen == a_pen:
                raise ValidationError("Penalty shootout cannot end in a draw.")
        else:
            # Clear penalty fields when not going to penalties.
            cleaned["penalty_winner"] = None
            cleaned["home_penalties"] = None
            cleaned["away_penalties"] = None
        return cleaned
