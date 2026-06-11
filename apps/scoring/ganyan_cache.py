"""GanyanScore + MatchPool upsert helpers driven by signals + management commands.

Unlike the legacy cache (per-user-per-slot), the ganyan computation is
inherently slot-scoped — base payouts depend on the full set of predictions
for the slot. So `recompute_slot` rewrites all rows for a slot in one shot.
"""

from decimal import Decimal

from django.db import transaction

from apps.tournament.models import BracketSlot

from .ganyan_bridge import compute_pre_result_pools, compute_slot_scores
from .models import GanyanScore, MatchPool


def recompute_slot(slot: BracketSlot) -> int:
    """Rebuild GanyanScore + MatchPool for one slot.

    Returns the number of GanyanScore rows written. Always rebuilds MatchPool
    rows (deleted + recreated to avoid stale criterions).
    """
    output = compute_slot_scores(slot)
    if output is None:
        # No result yet → no GanyanScore for played users (slot hasn't paid out).
        # Clear stale rows, then write pre-result MatchPool stats so the
        # ganyan tablosu UI shows live counts.
        return _recompute_pre_result(slot)

    user_scores, pool_stats = output
    return _recompute_post_result(slot, user_scores, pool_stats)


def _recompute_pre_result(slot: BracketSlot) -> int:
    """Slot has no ActualResult. Clear scores, refresh pool breakdowns."""
    with transaction.atomic():
        GanyanScore.objects.filter(slot=slot).delete()

        stats = compute_pre_result_pools(slot)
        MatchPool.objects.filter(slot=slot).delete()
        MatchPool.objects.bulk_create([
            MatchPool(
                slot=slot,
                criterion=s.criterion,
                pool_size=s.pool_size,
                predictor_count=s.predictor_count,
                winner_count=s.winner_count,
                base_payout=s.base_payout,
                breakdown=s.breakdown,
            )
            for s in stats
        ])
    return 0


def _recompute_post_result(slot: BracketSlot, user_scores, pool_stats) -> int:
    """Slot has an ActualResult. Upsert per-user GanyanScore + write MatchPool."""
    from apps.predictions.models import SlotPrediction
    from apps.tournament.models import PredictionRound

    # Map round_order → PredictionRound for the FK on GanyanScore.
    round_by_order = {
        r.order: r
        for r in PredictionRound.objects.filter(tournament=slot.tournament)
    }

    # User IDs in scope: anyone with a SlotPrediction for this slot.
    predictor_ids = set(
        SlotPrediction.objects.filter(slot=slot).values_list("user_id", flat=True).distinct()
    )

    with transaction.atomic():
        # Clear stale rows for users no longer in scope (e.g. prediction deleted).
        GanyanScore.objects.filter(slot=slot).exclude(user_id__in=predictor_ids).delete()

        for uid, score in user_scores.items():
            eff_round = round_by_order.get(score.effective_round_order)
            # "az yanlış" contribution: user predicted but scored 0.
            wrong = 1 if score.total == Decimal("0") else 0
            GanyanScore.objects.update_or_create(
                user_id=uid,
                slot=slot,
                defaults={
                    "score_exact": score.score_exact,
                    "score_diff": score.score_diff,
                    "score_result": score.score_result,
                    "score_penalty": score.score_penalty,
                    "total": score.total,
                    "effective_round": eff_round,
                    "outcome": score.outcome,
                    "wrong_count_contribution": wrong,
                },
            )

        # Pool stats.
        MatchPool.objects.filter(slot=slot).delete()
        MatchPool.objects.bulk_create([
            MatchPool(
                slot=slot,
                criterion=s.criterion,
                pool_size=s.pool_size,
                predictor_count=s.predictor_count,
                winner_count=s.winner_count,
                base_payout=s.base_payout,
                breakdown=s.breakdown,
            )
            for s in pool_stats
        ])

    return len(user_scores)


def recompute_all_slots_for_tournament(tournament) -> int:
    """Rebuild ganyan caches for every slot in a tournament. Used on cutover."""
    slots = BracketSlot.objects.filter(tournament=tournament)
    n = 0
    for slot in slots:
        n += recompute_slot(slot)
    return n
