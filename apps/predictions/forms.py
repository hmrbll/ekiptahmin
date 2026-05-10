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
from .standings import derive_best_third_for_slot, derive_group_team


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


def _resolve_slot_side_team(user, slot: BracketSlot, side: str):
    """Best-effort: figure out which Team belongs in `side` of `slot` for this user.

    Uses the same precedence chain as `SlotPredictionForm._configure_side`:
    actual (admin-set) → upstream slot cascade → group standings → best-third.
    Returns the Team or None if no path resolves (e.g. user hasn't predicted
    enough upstream slots yet).
    """
    actual = getattr(slot, f"{side}_team_actual")
    if actual:
        return actual

    source_slot = getattr(slot, f"{side}_source_slot")
    if source_slot:
        kind = getattr(slot, f"{side}_source_kind")
        return _derive_cascaded_team(user, source_slot, kind)

    group_letter = getattr(slot, f"{side}_source_group_letter")
    group_position = getattr(slot, f"{side}_source_group_position")
    if group_letter and group_position:
        return derive_group_team(user, slot.tournament, group_letter, group_position)

    thirds_groups = getattr(slot, f"{side}_source_thirds_groups")
    if thirds_groups:
        return derive_best_third_for_slot(user, slot.tournament, slot)

    return None


def _format_slot_blocker_label(user, source_slot: BracketSlot) -> str:
    """Label for a knockout cascade blocker. Prefers concrete team names
    (resolved via the user's earlier predictions or admin-entered actuals)
    and falls back to the textual `home_source` / `away_source` per side
    when no path resolves yet.
    """
    home_team = _resolve_slot_side_team(user, source_slot, "home")
    away_team = _resolve_slot_side_team(user, source_slot, "away")
    home_text = home_team.name_tr if home_team else (source_slot.home_source or "?")
    away_text = away_team.name_tr if away_team else (source_slot.away_source or "?")
    return f"{source_slot.position} — {home_text} vs {away_text}"


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
        # Set on the instance so ModelForm._post_clean → instance.full_clean()
        # can run model-level validation (penalty rules depend on slot.stage).
        self.instance.user = user
        self.instance.prediction_round = prediction_round
        self.instance.slot = slot
        # Each entry is {"label": str, "slot": BracketSlot|None}.
        # Slot present → clickable link to that slot's edit page.
        # Slot None → static label (e.g., "A Grubu 2.si" — fix by predicting group matches).
        self.cascade_blocked_on: list[dict] = []

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

        # Cascade resolution per side (knockout from R32 onward).
        # Three source kinds, in priority order:
        #   1. slot-derived (source_slot FK) — winner/loser of an earlier slot
        #   2. group-derived (group_letter + group_position) — "A Grubu 2.si"
        #   3. thirds-derived (thirds_groups) — "3.lerden biri (A/B/C)" — user picks
        # If no source is set, the field stays a free dropdown of all teams.
        self._configure_side(user, slot, "home")
        self._configure_side(user, slot, "away")

    def _configure_side(self, user, slot, side: str):
        field = self.fields[f"{side}_team"]
        source_slot = getattr(slot, f"{side}_source_slot")
        if source_slot:
            kind = getattr(slot, f"{side}_source_kind")
            team = _derive_cascaded_team(user, source_slot, kind)
            if team is None:
                self.cascade_blocked_on.append({
                    "label": _format_slot_blocker_label(user, source_slot),
                    "slot": source_slot,
                })
            else:
                field.initial = team
                field.disabled = True
            return

        group_letter = getattr(slot, f"{side}_source_group_letter")
        group_position = getattr(slot, f"{side}_source_group_position")
        if group_letter and group_position:
            team = derive_group_team(user, slot.tournament, group_letter, group_position)
            if team is None:
                self.cascade_blocked_on.append({
                    "label": f"{group_letter} Grubu {group_position}.si — grup maçlarını tahmin et",
                    "slot": None,
                })
            else:
                field.initial = team
                field.disabled = True
            return

        thirds_groups = getattr(slot, f"{side}_source_thirds_groups")
        if thirds_groups:
            # FIFA's Best-Third Allocation table picks exactly one of the 12
            # groups' third-place finishers for this slot, given which 8
            # qualify. The user doesn't choose — it falls out of their group
            # predictions deterministically.
            team = derive_best_third_for_slot(user, slot.tournament, slot)
            if team is None:
                letters = [c for c in thirds_groups.split(",") if c.strip()]
                self.cascade_blocked_on.append({
                    "label": f"3.lerden biri ({'/'.join(letters)}) — tüm 12 grubun maçlarını tahmin et",
                    "slot": None,
                })
            else:
                field.initial = team
                field.disabled = True
            return

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
        # FKs already set in __init__ on self.instance — re-assert for clarity.
        instance.user = self.user
        instance.prediction_round = self.prediction_round
        instance.slot = self.slot
        if commit:
            instance.save()
        return instance
