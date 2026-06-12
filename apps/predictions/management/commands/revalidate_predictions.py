"""Backfill sweep: delete stale knockout predictions in all OPEN rounds.

The save-time invalidation pass (apps/predictions/cascade.py) keeps a
user's bracket consistent from the moment it shipped — but predictions
edited before that deploy can still hold matchups their upstream
predictions no longer derive (e.g. a group edit moved a team to a
different R32 slot while the old form silently kept the stored teams).
Nothing re-checks those rows until the user happens to re-save upstream,
so they linger.

This command applies the same product rule retroactively: any knockout
prediction in an open round whose matchup is no longer derivable is
deleted ("looks never predicted") so the user can re-enter it while the
round is still open. Closed rounds are scored history and are never
touched — which is also why this is an ops command, not a build hook:
run it deliberately, after a bug let stale rows through.

Usage:
  python manage.py revalidate_predictions --dry-run   # report only
  python manage.py revalidate_predictions             # delete stale rows
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.predictions.cascade import invalidate_stale_predictions
from apps.tournament.models import PredictionRound


class Command(BaseCommand):
    help = "Delete stale knockout predictions (matchup no longer derivable) in all open rounds."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be deleted, then roll back.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        open_rounds = [r for r in PredictionRound.objects.all() if r.is_open]
        if not open_rounds:
            self.stdout.write("No open rounds — nothing to revalidate.")
            return

        User = get_user_model()
        total = 0
        # Single transaction so a dry run can report the full cascade
        # (deleting a stale R32 row makes dependent R16/QF rows stale on
        # the same pass) and still roll everything back.
        with transaction.atomic():
            for pr in open_rounds:
                user_ids = (
                    pr.slot_predictions.values_list("user_id", flat=True).distinct()
                )
                for user in User.objects.filter(pk__in=user_ids):
                    deleted = invalidate_stale_predictions(user, pr)
                    if not deleted:
                        continue
                    total += len(deleted)
                    positions = ", ".join(s.position for s in deleted)
                    self.stdout.write(
                        f"  {pr.name} / {user.nickname or user.email}: "
                        f"{len(deleted)} stale -> {positions}"
                    )
            if dry_run:
                transaction.set_rollback(True)

        verb = "would be deleted (dry run)" if dry_run else "deleted"
        style = self.style.WARNING if total else self.style.SUCCESS
        self.stdout.write(style(f"{total} stale prediction(s) {verb}."))
