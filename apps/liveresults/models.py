"""Per-slot live-sync state for the football-data integration.

One row per BracketSlot we track. Holds the volatile provider state (status,
minute) plus the two things the poller needs: the `external_id` mapping and
the `finalized` flag. ActualResult stays the canonical score record; this is
purely the sync's bookkeeping so the homepage can show a "CANLI" badge and the
poller knows which matches to stop requesting.
"""

from __future__ import annotations

from django.db import models

from apps.tournament.models import BracketSlot


class MatchSync(models.Model):
    """Live-sync bookkeeping for one match (BracketSlot)."""

    # football-data status values we care about. IN_PLAY / PAUSED = "live now".
    STATUS_SCHEDULED = "SCHEDULED"
    STATUS_TIMED = "TIMED"
    STATUS_IN_PLAY = "IN_PLAY"
    STATUS_PAUSED = "PAUSED"
    STATUS_FINISHED = "FINISHED"
    STATUS_SUSPENDED = "SUSPENDED"
    STATUS_POSTPONED = "POSTPONED"
    STATUS_CANCELLED = "CANCELLED"
    LIVE_STATUSES = frozenset({STATUS_IN_PLAY, STATUS_PAUSED})

    slot = models.OneToOneField(
        BracketSlot, on_delete=models.CASCADE, related_name="live_sync",
    )
    external_id = models.CharField(
        max_length=32, blank=True, db_index=True,
        help_text="Provider match id (football-data). Blank until mapped.",
    )
    provider = models.CharField(max_length=32, default="football-data")
    status = models.CharField(
        max_length=20, blank=True,
        help_text="Latest provider status (SCHEDULED, IN_PLAY, PAUSED, FINISHED, ...).",
    )
    minute = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Live match minute, when the provider reports it.",
    )
    injury_time = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Added/stoppage minutes (provider injuryTime); shown as 90+N′.",
    )
    finalized = models.BooleanField(
        default=False,
        help_text="FINISHED has been captured. The poller skips this slot from now on.",
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "match sync"
        verbose_name_plural = "match syncs"

    def __str__(self) -> str:
        return f"{self.slot.position} [{self.status or '—'}{'/final' if self.finalized else ''}]"

    @property
    def is_live(self) -> bool:
        """Currently in play (drives the homepage 'CANLI' module)."""
        return self.status in self.LIVE_STATUSES and not self.finalized
