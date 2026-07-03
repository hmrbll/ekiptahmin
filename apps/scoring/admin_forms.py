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
        # Drop the flag emoji from option labels: Windows renders it as a bare
        # two-letter code ("br Brezilya"). The wizard shows real SVG flags for
        # resolved teams; this picker only appears for not-yet-resolved slots.
        for name in ("home_team_actual", "away_team_actual"):
            self.fields[name].label_from_instance = (
                lambda team: f"{team.name_tr} ({team.code})"
            )

    def clean(self):
        cleaned = super().clean()
        h = cleaned.get("home_team_actual")
        a = cleaned.get("away_team_actual")
        if h and a and h.id == a.id:
            # English: this form renders only on the /admin/results/ wizard.
            raise ValidationError("Home and away cannot be the same team.")
        return cleaned


class ActualResultForm(forms.ModelForm):
    """Score + extra-time / penalty fields on the slot's ActualResult.

    Extra time and penalties are derived from the score shape, never manual
    flags: a knockout level after 90' always went to extra time (so the 120'
    score is required), and one still level after 120' was decided on
    penalties (so the shootout fields are required).
    """

    class Meta:
        model = ActualResult
        fields = [
            "home_score", "away_score",
            "home_score_aet", "away_score_aet",
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
        for name in (
            "home_score_aet", "away_score_aet",
            "penalty_winner", "home_penalties", "away_penalties",
        ):
            self.fields[name].required = False

    def clean(self):
        cleaned = super().clean()
        home = cleaned.get("home_score")
        away = cleaned.get("away_score")
        aet_home = cleaned.get("home_score_aet")
        aet_away = cleaned.get("away_score_aet")
        pen_winner = cleaned.get("penalty_winner")
        h_pen = cleaned.get("home_penalties")
        a_pen = cleaned.get("away_penalties")

        # (English: this form renders only under /admin/.)
        is_knockout = self.slot.stage.kind != "GROUP"
        drawn_90 = home is not None and away is not None and home == away

        if not (is_knockout and drawn_90):
            # Group match, or a knockout decided in 90' — nothing beyond
            # regulation, stray posted fields are cleared.
            cleaned["went_to_extra_time"] = False
            cleaned["went_to_penalties"] = False
            cleaned["home_score_aet"] = None
            cleaned["away_score_aet"] = None
            cleaned["penalty_winner"] = None
            cleaned["home_penalties"] = None
            cleaned["away_penalties"] = None
            return cleaned

        # Knockout level after 90' → extra time was played; the 120' score is
        # what the resolver picks the winner from, so it's required.
        cleaned["went_to_extra_time"] = True
        if aet_home is None or aet_away is None:
            raise ValidationError("Extra-time (120') score is required for a drawn knockout.")
        if aet_home < home or aet_away < away:
            raise ValidationError("The 120' score cannot be lower than the 90' score.")

        went_pen = aet_home == aet_away
        cleaned["went_to_penalties"] = went_pen

        if went_pen:
            if not pen_winner:
                raise ValidationError("Penalty shootout winner is required.")
            if h_pen is None or a_pen is None:
                raise ValidationError("Penalty score is required for both sides.")
            if h_pen == a_pen:
                raise ValidationError("Penalty shootout cannot end in a draw.")
        else:
            # Decided in extra time — no shootout fields.
            cleaned["penalty_winner"] = None
            cleaned["home_penalties"] = None
            cleaned["away_penalties"] = None
        return cleaned
