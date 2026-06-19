"""Resolve actual bracket team assignments from recorded results (idempotent).

Normally runs automatically on every result save; this is for manual ops —
e.g. after a bulk import, or to re-fill knockout slots after editing a result.

    python manage.py resolve_bracket
"""

from django.core.management.base import BaseCommand

from apps.tournament.models import Tournament
from apps.tournament.resolver import resolve_bracket


class Command(BaseCommand):
    help = "Fill derivable bracket slot teams from current results. Idempotent."

    def handle(self, *args, **opts):
        tournament = Tournament.objects.filter(is_active=True).first()
        if tournament is None:
            self.stderr.write(self.style.ERROR("No active tournament."))
            return
        changed = resolve_bracket(tournament)
        for slot in changed:
            self.stdout.write(
                f"  {slot.position}: {slot.home_team_actual} vs {slot.away_team_actual}"
            )
        self.stdout.write(self.style.HTTP_INFO(f"resolved {len(changed)} slot(s)"))
