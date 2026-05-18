"""Cache invalidation signals for SlotScore.

Two write paths trigger recompute:
1. ActualResult save/delete  → every user × that slot
2. SlotPrediction save/delete → that user × that slot (across all rounds —
   the engine picks the highest-scoring correct prediction across rounds,
   so any round-edit can change the verdict)

Recompute is synchronous (small friend-group scale; ~30 users). If this gets
heavy later, swap in a worker queue here without changing callers.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.predictions.models import SlotPrediction
from apps.tournament.models import ActualResult

from .cache import recompute_slot_for_all_users, recompute_slot_for_user
from .models import SlotScore


@receiver(post_save, sender=ActualResult)
def _on_actual_result_saved(sender, instance: ActualResult, **kwargs):
    recompute_slot_for_all_users(instance.slot)


@receiver(post_delete, sender=ActualResult)
def _on_actual_result_deleted(sender, instance: ActualResult, **kwargs):
    # No result anymore → every user's score for this slot reverts to
    # NO_RESULT / zero. recompute_slot_for_user handles that path.
    recompute_slot_for_all_users(instance.slot)
    # Slots with predictions but no result still get a NO_RESULT row above.
    # Anything that wasn't in the predictions set never had a row to begin with.


@receiver(post_save, sender=SlotPrediction)
def _on_prediction_saved(sender, instance: SlotPrediction, **kwargs):
    recompute_slot_for_user(instance.user, instance.slot)


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
