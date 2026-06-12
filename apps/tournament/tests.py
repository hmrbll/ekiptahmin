"""seed_wc2026 — PredictionRounds are admin-owned after creation.

During the live tournament the admin closes stages mid-round (GROUP removed
from "Pre-turnuva" at kickoff) and moves deadlines. build.sh re-runs the seed
on every deploy, so a re-seed must never revert those edits.
"""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.tournament.models import PredictionRound, Tournament


@pytest.mark.django_db
class TestReseedPreservesAdminOwnedRounds:
    def _seed(self):
        call_command("seed_wc2026", verbosity=0)
        return Tournament.objects.get(slug="wc2026")

    def test_reseed_keeps_admin_removed_stage_and_moved_deadline(self):
        tournament = self._seed()
        rnd = PredictionRound.objects.get(tournament=tournament, order=0)
        assert rnd.editable_stages.filter(kind="GROUP").exists()

        # Admin closes the group stage mid-round and extends the deadline.
        group_stage = rnd.editable_stages.get(kind="GROUP")
        rnd.editable_stages.remove(group_stage)
        new_deadline = timezone.now() + timedelta(days=1)
        rnd.deadline = new_deadline
        rnd.save()

        self._seed()  # deploy re-runs the seed

        rnd.refresh_from_db()
        assert not rnd.editable_stages.filter(kind="GROUP").exists()
        assert rnd.deadline == new_deadline

    def test_reseed_keeps_admin_created_round(self):
        tournament = self._seed()
        extra = PredictionRound.objects.create(
            tournament=tournament, name="Özel tur", order=99,
            deadline=timezone.now() + timedelta(days=30), weight="0.30",
        )

        self._seed()

        assert PredictionRound.objects.filter(pk=extra.pk).exists()
