"""Seed the 2026 World Cup tournament structure from data/wc2026/.

Idempotent: rerunning updates existing rows in place rather than duplicating.
Reads:
- data/wc2026/tournament.json    (Tournament + Stages + PredictionRounds)
- data/wc2026/teams.csv          (Teams)
- data/wc2026/group_matches.csv  (BracketSlots for group stage)
- data/wc2026/knockout_slots.csv (BracketSlots for knockout stages)
"""

import csv
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.tournament.models import (
    BracketSlot,
    PredictionRound,
    Stage,
    Team,
    Tournament,
)

DATA_DIR = Path(settings.BASE_DIR) / "data" / "wc2026"

# Matches "R32-1 Kazananı", "SF-2 Mağlubu", etc. Returns (position, kind) or None.
_SOURCE_RE = re.compile(r"^(R32|R16|QF|SF|Third|Final)-?(\d*)\s+(Kazananı|Mağlubu)$")


def _parse_source(source_str: str) -> tuple[str | None, str]:
    """Parse a knockout source description into (position, kind).

    Returns (position, "WINNER"|"LOSER") for slot-derived sources,
    (None, "WINNER") for group-derived sources (R32 typically).
    """
    s = (source_str or "").strip()
    m = _SOURCE_RE.match(s)
    if not m:
        return (None, BracketSlot.SOURCE_KIND_WINNER)
    base, num, label = m.group(1), m.group(2), m.group(3)
    position = f"{base}-{num}" if num else base
    kind = (
        BracketSlot.SOURCE_KIND_WINNER if label == "Kazananı"
        else BracketSlot.SOURCE_KIND_LOSER
    )
    return (position, kind)


class Command(BaseCommand):
    help = "Seed the 2026 World Cup tournament structure (idempotent)."

    def handle(self, *args, **options):
        with transaction.atomic():
            tournament = self._seed_tournament_config()
            self._seed_teams(tournament)
            self._seed_group_matches(tournament)
            self._seed_knockout_slots(tournament)
        self.stdout.write(self.style.SUCCESS("Seed complete."))

    # ---- Tournament + Stages + PredictionRounds (from JSON) ----

    def _seed_tournament_config(self) -> Tournament:
        path = DATA_DIR / "tournament.json"
        if not path.exists():
            raise FileNotFoundError(f"Missing seed file: {path}")

        config = json.loads(path.read_text(encoding="utf-8"))
        tdata = config["tournament"]

        tournament, created = Tournament.objects.update_or_create(
            slug=tdata["slug"],
            defaults={
                "name": tdata["name"],
                "start_date": tdata["start_date"],
                "end_date": tdata["end_date"],
                "timezone": tdata["timezone"],
                "is_active": tdata["is_active"],
            },
        )
        self._log(f"Tournament: {tournament.name}", created)

        stage_by_kind = {}
        for sd in config["stages"]:
            stage, created = Stage.objects.update_or_create(
                tournament=tournament,
                kind=sd["kind"],
                defaults={
                    "order": sd["order"],
                    "points_exact": sd["points_exact"],
                    "points_diff": sd["points_diff"],
                    "points_result": sd["points_result"],
                    "penalty_loser_pct": Decimal(sd["penalty_loser_pct"]),
                },
            )
            stage_by_kind[stage.kind] = stage
            self._log(f"  Stage: {stage.get_kind_display()}", created)

        for rd in config["prediction_rounds"]:
            round_obj, created = PredictionRound.objects.update_or_create(
                tournament=tournament,
                order=rd["order"],
                defaults={
                    "name": rd["name"],
                    "deadline": datetime.fromisoformat(rd["deadline_iso"]),
                    "weight": Decimal(rd["weight"]),
                },
            )
            round_obj.editable_stages.set(
                [stage_by_kind[k] for k in rd["editable_stages"] if k in stage_by_kind]
            )
            self._log(f"  Round: {round_obj.name} (×{round_obj.weight})", created)

        return tournament

    # ---- Teams (from CSV) ----

    def _seed_teams(self, tournament: Tournament) -> None:
        path = DATA_DIR / "teams.csv"
        if not path.exists():
            self.stdout.write(self.style.WARNING(f"Skipping teams: {path} not found yet."))
            return

        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                team, created = Team.objects.update_or_create(
                    tournament=tournament,
                    code=row["code"].strip().upper(),
                    defaults={
                        "name_tr": row["name_tr"].strip(),
                        "flag_emoji": row.get("flag_emoji", "").strip(),
                        "group_letter": row.get("group_letter", "").strip().upper(),
                    },
                )
                self._log(f"  Team: {team}", created)

    # ---- Group stage BracketSlots (from CSV) ----

    def _seed_group_matches(self, tournament: Tournament) -> None:
        path = DATA_DIR / "group_matches.csv"
        if not path.exists():
            self.stdout.write(self.style.WARNING(f"Skipping group matches: {path} not found yet."))
            return

        group_stage = Stage.objects.get(tournament=tournament, kind=Stage.GROUP)
        teams = {t.code: t for t in tournament.teams.all()}

        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                position = f"Group{row['group'].strip().upper()}-M{row['match_no'].strip()}"
                home = teams.get(row["home_code"].strip().upper())
                away = teams.get(row["away_code"].strip().upper())
                if home is None or away is None:
                    self.stdout.write(self.style.ERROR(
                        f"  Skipping {position}: unknown team code(s) {row['home_code']}/{row['away_code']}"
                    ))
                    continue

                slot, created = BracketSlot.objects.update_or_create(
                    tournament=tournament,
                    position=position,
                    defaults={
                        "stage": group_stage,
                        "scheduled_kickoff": datetime.fromisoformat(row["kickoff_iso"]),
                        "venue": row.get("venue", "").strip(),
                        "home_team_actual": home,
                        "away_team_actual": away,
                    },
                )
                self._log(f"  Group slot: {position}", created)

    # ---- Knockout BracketSlots (from CSV) ----

    def _seed_knockout_slots(self, tournament: Tournament) -> None:
        path = DATA_DIR / "knockout_slots.csv"
        if not path.exists():
            self.stdout.write(self.style.WARNING(f"Skipping knockout slots: {path} not found yet."))
            return

        stages = {s.kind: s for s in tournament.stages.all()}

        # Two-pass: first create/update all slots, then resolve source FKs
        # (a R16 slot's source is an R32 slot which must already exist).
        rows: list[dict] = []
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows.append(row)

        for row in rows:
            position = row["position"].strip()
            stage_kind = row["stage_kind"].strip().upper()
            stage = stages.get(stage_kind)
            if stage is None:
                self.stdout.write(self.style.ERROR(
                    f"  Skipping {position}: unknown stage_kind '{stage_kind}'"
                ))
                continue

            slot, created = BracketSlot.objects.update_or_create(
                tournament=tournament,
                position=position,
                defaults={
                    "stage": stage,
                    "scheduled_kickoff": datetime.fromisoformat(row["kickoff_iso"]),
                    "venue": row.get("venue", "").strip(),
                    "home_source": row.get("home_source", "").strip(),
                    "away_source": row.get("away_source", "").strip(),
                },
            )
            self._log(f"  Knockout slot: {position}", created)

        # Pass 2: resolve cascade FKs.
        slots_by_position = {
            s.position: s
            for s in BracketSlot.objects.filter(tournament=tournament)
        }
        for row in rows:
            position = row["position"].strip()
            slot = slots_by_position.get(position)
            if slot is None:
                continue

            home_pos, home_kind = _parse_source(row.get("home_source", ""))
            away_pos, away_kind = _parse_source(row.get("away_source", ""))
            home_src = slots_by_position.get(home_pos) if home_pos else None
            away_src = slots_by_position.get(away_pos) if away_pos else None

            BracketSlot.objects.filter(pk=slot.pk).update(
                home_source_slot=home_src,
                home_source_kind=home_kind,
                away_source_slot=away_src,
                away_source_kind=away_kind,
            )

    # ---- Helpers ----

    def _log(self, label: str, created: bool) -> None:
        verb = "created" if created else "updated"
        self.stdout.write(f"{label} ({verb})")
