"""Rebuild every GanyanScore + MatchPool row for one tournament from scratch.

Use after schema changes to the ganyan engine, after pool size edits in
Stage admin (signals don't fire on Stage saves), or as a recovery if signal
runs were missed. Idempotent — cache writer upserts and clears stale rows.
"""

from django.core.management.base import BaseCommand

from apps.scoring.ganyan_cache import recompute_slot
from apps.tournament.models import BracketSlot, Tournament


class Command(BaseCommand):
    help = "Recompute GanyanScore + MatchPool for every slot in a tournament."

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

        slots = list(BracketSlot.objects.filter(tournament=tournament))
        self.stdout.write(
            f"Recomputing ganyan caches for {len(slots)} slot(s) on {tournament.name}..."
        )

        total_user_rows = 0
        for slot in slots:
            n = recompute_slot(slot)
            total_user_rows += n
        self.stdout.write(self.style.SUCCESS(
            f"Done. {len(slots)} slot(s) processed, {total_user_rows} GanyanScore row(s) written."
        ))
