"""Cache invalidation signals for both scoring engines.

Two write paths trigger recompute:
1. ActualResult save/delete  → every user × that slot, both engines.
2. SlotPrediction save/delete → that user × that slot (legacy), full slot (ganyan).

Recompute is synchronous (small friend-group scale; ~30 users). If this gets
heavy later, swap in a worker queue here without changing callers.

Both engines run side by side: legacy SlotScore drives /legacy/* staff views,
GanyanScore drives the public site. Order: legacy first (cheaper / per-user),
ganyan second (slot-scoped, recomputes pools every time).
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.predictions.models import SlotPrediction
from apps.tournament.models import ActualResult

from .cache import recompute_slot_for_all_users, recompute_slot_for_user
from .ganyan_cache import recompute_slot as recompute_ganyan_slot
from .models import SlotScore


@receiver(post_save, sender=ActualResult)
def _on_actual_result_saved(sender, instance: ActualResult, **kwargs):
    recompute_slot_for_all_users(instance.slot)
    recompute_ganyan_slot(instance.slot)


@receiver(post_delete, sender=ActualResult)
def _on_actual_result_deleted(sender, instance: ActualResult, **kwargs):
    # No result anymore → every user's score for this slot reverts to
    # NO_RESULT / zero. recompute_slot_for_user handles that path.
    recompute_slot_for_all_users(instance.slot)
    # Ganyan: clears scores, writes pre-result pool breakdown.
    recompute_ganyan_slot(instance.slot)


@receiver(post_save, sender=SlotPrediction)
def _on_prediction_saved(sender, instance: SlotPrediction, **kwargs):
    recompute_slot_for_user(instance.user, instance.slot)
    # Ganyan pools shift with each new prediction (changes |W_c| and the
    # tablosu breakdown), so we rebuild the whole slot.
    recompute_ganyan_slot(instance.slot)


@receiver(post_delete, sender=SlotPrediction)
def _on_prediction_deleted(sender, instance: SlotPrediction, **kwargs):
    # The user may still have predictions for this slot in other rounds —
    # let recompute_slot_for_user re-evaluate from whatever's left. If no
    # predictions remain, the engine returns the "no_prediction" breakdown
    # and we store a zero-point row.
    if SlotPrediction.objects.filter(user=instance.user, slot=instance.slot).exists():
        recompute_slot_for_user(instance.user, instance.slot)
    else:
        # Clear the row — user has no predictions for this slot anymore.
        SlotScore.objects.filter(user=instance.user, slot=instance.slot).delete()
    # Ganyan: pool composition changed, rebuild whole slot.
    recompute_ganyan_slot(instance.slot)
