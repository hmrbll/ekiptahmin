"""Rebuild every SlotScore row from scratch.

Use after schema changes to the engine or as a recovery if signals missed
writes. Idempotent — the cache writer upserts (user, slot) pairs.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.scoring.cache import recompute_user_all_slots
from apps.tournament.models import Tournament


class Command(BaseCommand):
    help = "Recompute SlotScore for every user across every slot in the active tournament."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tournament-slug", default=None,
            help="Tournament slug (defaults to the is_active=True tournament).",
        )

    def handle(self, *args, **options):
        slug = options.get("tournament_slug")
        if slug:
            tournament = Tournament.objects.get(slug=slug)
        else:
            tournament = Tournament.objects.filter(is_active=True).first()
            if tournament is None:
                self.stdout.write(self.style.ERROR("No active tournament found."))
                return

        User = get_user_model()
        users = list(User.objects.all())
        self.stdout.write(f"Recomputing for {len(users)} user(s) on {tournament.name}...")

        total = 0
        for user in users:
            n = recompute_user_all_slots(user, tournament)
            total += n
            self.stdout.write(f"  {user}: {n} slot(s)")
        self.stdout.write(self.style.SUCCESS(f"Done. {total} (user, slot) row(s) processed."))
