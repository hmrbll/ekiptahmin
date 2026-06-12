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

Derivation helpers live in `cascade.py` (shared with the post-save
invalidation pass that deletes stale downstream predictions). The derived
team is written into `self.initial` as well as `field.initial` — a disabled
field's submit value comes from `self.initial`, so this is what guarantees a
stale team stored on an existing prediction can never survive a re-save.
"""

from django import forms
from django.core.exceptions import ValidationError

from apps.tournament.models import BracketSlot, Team

from .cascade import derive_cascaded_team, resolve_slot_side_team
from .models import SlotPrediction
from .standings import derive_best_third_for_slot, derive_group_team


def _format_slot_blocker_label(user, source_slot: BracketSlot) -> str:
    """Label for a knockout cascade blocker. Prefers concrete team names
    (resolved via the user's earlier predictions or admin-entered actuals)
    and falls back to the textual `home_source` / `away_source` per side
    when no path resolves yet.
    """
    home_team = resolve_slot_side_team(user, source_slot, "home")
    away_team = resolve_slot_side_team(user, source_slot, "away")
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
            self._lock_side_to_team("home", slot.home_team_actual)
            self._lock_side_to_team("away", slot.away_team_actual)
            return

        # Cascade resolution per side (knockout from R32 onward).
        # Three source kinds, in priority order:
        #   1. slot-derived (source_slot FK) — winner/loser of an earlier slot
        #   2. group-derived (group_letter + group_position) — "A Grubu 2.si"
        #   3. thirds-derived (thirds_groups) — "3.lerden biri (A/B/C)" — user picks
        # If no source is set, the field stays a free dropdown of all teams.
        self._configure_side(user, slot, "home")
        self._configure_side(user, slot, "away")

    def _lock_side_to_team(self, side: str, team):
        """Pin a team field to the derived/fixed team and disable it.

        Written to BOTH `field.initial` and `self.initial`: Django resolves a
        disabled field's value via `self.initial` first, and ModelForm seeds
        that dict from the instance — so without the override, a stale team
        stored on an existing prediction would silently win over the freshly
        derived one (both in display and on save).
        """
        field = self.fields[f"{side}_team"]
        field.initial = team
        field.disabled = True
        self.initial[f"{side}_team"] = team

    def _configure_side(self, user, slot, side: str):
        source_slot = getattr(slot, f"{side}_source_slot")
        if source_slot:
            kind = getattr(slot, f"{side}_source_kind")
            team = derive_cascaded_team(user, source_slot, kind)
            if team is None:
                self.cascade_blocked_on.append({
                    "label": _format_slot_blocker_label(user, source_slot),
                    "slot": source_slot,
                })
            else:
                self._lock_side_to_team(side, team)
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
                self._lock_side_to_team(side, team)
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
                self._lock_side_to_team(side, team)
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
        # When an edit turns a draw into a decisive score, the browser only
        # CSS-hides the penalty section — its stale inputs still submit. A
        # decisive scoreline makes shootout fields meaningless, so drop them
        # here; letting model validation reject them would fail the save with
        # an error that renders inside the hidden section (invisible).
        home_score = cleaned.get("home_score")
        away_score = cleaned.get("away_score")
        if home_score is not None and away_score is not None and home_score != away_score:
            cleaned["penalty_winner"] = None
            cleaned["home_penalties"] = None
            cleaned["away_penalties"] = None
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
