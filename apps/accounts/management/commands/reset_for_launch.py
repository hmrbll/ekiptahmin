"""One-shot production cleanup before going live: wipe all test data while
keeping staff accounts.

What it does:
- Deletes every non-staff user. CASCADE removes their SlotPrediction,
  BracketCompletionEvent, GanyanScore and SlotScore rows along with them.
- For the staff users it keeps (Hemre), deletes their SlotPrediction and
  BracketCompletionEvent rows so their bracket starts empty.
- Wipes all score caches (GanyanScore, SlotScore, MatchPool). With no
  predictions left these would be stale anyway; recompute_ganyan on the next
  deploy rebuilds them empty.
- Deletes all ActualResult rows and clears knockout slots' resolved teams
  (home_team_actual / away_team_actual) back to NULL. Group slots keep their
  seeded teams.

Invites are left untouched (send fresh ones with `send_invites`).

Safety: dry-run by default. Pass --confirm to actually delete. Run once from
Render Shell — do NOT add to build.sh (it would wipe data on every deploy).

  python manage.py reset_for_launch              # preview only
  python manage.py reset_for_launch --confirm    # actually delete
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.predictions.models import BracketCompletionEvent, SlotPrediction
from apps.scoring.models import GanyanScore, MatchPool, SlotScore
from apps.tournament.models import ActualResult, BracketSlot


class Command(BaseCommand):
    help = "Wipe test data (predictions + non-staff users) before launch. Keeps staff accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm", action="store_true",
            help="Actually perform the deletion. Without this flag, only a preview is shown.",
        )

    def handle(self, *args, confirm, **options):
        User = get_user_model()

        keep_users = list(User.objects.filter(is_staff=True))
        delete_users = list(User.objects.filter(is_staff=False))

        self.stdout.write(self.style.MIGRATE_HEADING("Plan"))
        self.stdout.write(f"  Keep (staff)      : {len(keep_users)} user(s)")
        for u in keep_users:
            self.stdout.write(f"      ✓ {u.email} (nickname={u.nickname or '—'})")
        self.stdout.write(f"  Delete (non-staff): {len(delete_users)} user(s)")
        for u in delete_users:
            self.stdout.write(f"      ✗ {u.email} (nickname={u.nickname or '—'})")

        self.stdout.write(self.style.MIGRATE_HEADING("Rows before"))
        self.stdout.write(f"  SlotPrediction        : {SlotPrediction.objects.count()}")
        self.stdout.write(f"  BracketCompletionEvent: {BracketCompletionEvent.objects.count()}")
        self.stdout.write(f"  GanyanScore           : {GanyanScore.objects.count()}")
        self.stdout.write(f"  SlotScore             : {SlotScore.objects.count()}")
        self.stdout.write(f"  MatchPool             : {MatchPool.objects.count()}")
        self.stdout.write(f"  ActualResult          : {ActualResult.objects.count()}")
        self.stdout.write(f"  Knockout slots w/ resolved teams: {self._resolved_knockout_qs().count()}")

        if not keep_users:
            self.stdout.write(self.style.ERROR(
                "No staff user found — aborting. Refusing to delete every user. "
                "Mark your account is_staff=True first."
            ))
            return

        if not confirm:
            self.stdout.write(self.style.WARNING(
                "\nDry-run — nothing changed. Re-run with --confirm to execute."
            ))
            return

        keep_ids = [u.id for u in keep_users]
        with transaction.atomic():
            # Clear kept users' predictions + completion events.
            preds = SlotPrediction.objects.filter(user_id__in=keep_ids).delete()[0]
            events = BracketCompletionEvent.objects.filter(user_id__in=keep_ids).delete()[0]
            # Delete non-staff users (CASCADE removes their predictions/scores).
            removed_users = User.objects.filter(is_staff=False).delete()[0]
            # Delete actual results + revert resolved knockout teams to NULL.
            results = ActualResult.objects.all().delete()[0]
            knockout_reset = self._resolved_knockout_qs().update(
                home_team_actual=None, away_team_actual=None,
            )
            # Wipe all score caches — no predictions remain, so these are stale.
            # (ActualResult delete fires recompute signals; this wipe is the final word.)
            GanyanScore.objects.all().delete()
            SlotScore.objects.all().delete()
            MatchPool.objects.all().delete()

        self.stdout.write(self.style.MIGRATE_HEADING("Done"))
        self.stdout.write(f"  Kept users' predictions deleted : {preds}")
        self.stdout.write(f"  Kept users' completion events   : {events}")
        self.stdout.write(f"  Objects removed with non-staff users (incl. CASCADE): {removed_users}")
        self.stdout.write(f"  ActualResult rows deleted       : {results}")
        self.stdout.write(f"  Knockout slots reverted to NULL : {knockout_reset}")
        self.stdout.write(self.style.MIGRATE_HEADING("Rows after"))
        self.stdout.write(f"  SlotPrediction        : {SlotPrediction.objects.count()}")
        self.stdout.write(f"  BracketCompletionEvent: {BracketCompletionEvent.objects.count()}")
        self.stdout.write(f"  GanyanScore           : {GanyanScore.objects.count()}")
        self.stdout.write(f"  SlotScore             : {SlotScore.objects.count()}")
        self.stdout.write(f"  MatchPool             : {MatchPool.objects.count()}")
        self.stdout.write(f"  ActualResult          : {ActualResult.objects.count()}")
        self.stdout.write(f"  Knockout slots w/ resolved teams: {self._resolved_knockout_qs().count()}")
        self.stdout.write(self.style.SUCCESS("\nClean slate ready. Staff accounts kept; predictions + results cleared."))

    @staticmethod
    def _resolved_knockout_qs():
        """Knockout slots (non-GROUP) that currently have at least one resolved team."""
        from django.db.models import Q

        return BracketSlot.objects.exclude(stage__kind="GROUP").filter(
            Q(home_team_actual__isnull=False) | Q(away_team_actual__isnull=False)
        )
