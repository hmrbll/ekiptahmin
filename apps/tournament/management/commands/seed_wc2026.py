"""Seed the 2026 World Cup tournament structure from data/wc2026/.

Idempotent: rerunning updates existing rows in place rather than duplicating.
Exception: PredictionRounds are created once and then admin-owned — deploys
must not revert mid-tournament admin edits (closed stages, moved deadlines).
Reads:
- data/wc2026/tournament.json    (Tournament + Stages + PredictionRounds)
- data/wc2026/teams.csv          (Teams)
- data/wc2026/group_matches.csv  (BracketSlots for group stage)
- data/wc2026/knockout_slots.csv (BracketSlots for knockout stages)
"""

import csv
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Max, Min

from apps.tournament.models import (
    BracketSlot,
    PredictionRound,
    Stage,
    Team,
    Tournament,
)

DATA_DIR = Path(settings.BASE_DIR) / "data" / "wc2026"

# Buffer added to a stage's last kickoff to compute the next round's opens_at.
# Knockout matches can run 90' + 30' ET + 30' penalties + margin → 180 min.
# Group matches don't go to ET/penalties, so a tighter 110 min is enough.
_STAGE_BUFFER_MINUTES = {
    Stage.GROUP: 110,
    Stage.R32: 180,
    Stage.R16: 180,
    Stage.QF: 180,
    Stage.SF: 180,
    Stage.THIRD: 180,
}

# Matches "R32-1 Kazananı", "SF-2 Mağlubu", etc. Returns (position, kind) or None.
_SOURCE_RE = re.compile(r"^(R32|R16|QF|SF|Third|Final)-?(\d*)\s+(Kazananı|Mağlubu)$")
# Matches "A Grubu 2.si", "F Grubu 1.si", "C Grubu 3.sü"
_GROUP_RE = re.compile(r"^([A-L])\s+Grubu\s+(\d+)\.s[iü]$")
# Matches "3.lerden biri (A/B/C/D/F)"
_THIRDS_RE = re.compile(r"^3\.lerden\s+biri\s*\(([A-L/]+)\)$")


def _parse_source(source_str: str) -> tuple[str | None, str]:
    """Parse a knockout slot-source description into (position, kind).

    Returns (position, "WINNER"|"LOSER") for slot-derived sources,
    (None, "WINNER") otherwise. Group-derived sources are parsed by
    `_parse_group_source` instead.
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


def _parse_group_source(source_str: str) -> dict:
    """Parse a group-derived source description into structured fields.

    Returns one of:
    - {"group_letter": "A", "group_position": 2}     for "A Grubu 2.si"
    - {"thirds_groups": "A,B,C,D,F"}                  for "3.lerden biri (A/B/C/D/F)"
    - {} otherwise
    """
    s = (source_str or "").strip()
    m = _GROUP_RE.match(s)
    if m:
        return {"group_letter": m.group(1), "group_position": int(m.group(2))}
    m = _THIRDS_RE.match(s)
    if m:
        letters = [c for c in m.group(1) if c.isalpha()]
        return {"thirds_groups": ",".join(letters)}
    return {}


class Command(BaseCommand):
    help = "Seed the 2026 World Cup tournament structure (idempotent)."

    def handle(self, *args, **options):
        with transaction.atomic():
            tournament, config = self._seed_tournament_and_stages()
            self._seed_teams(tournament)
            self._seed_group_matches(tournament)
            self._seed_knockout_slots(tournament)
            # Rounds depend on slot data (opens_at = last source-stage kickoff
            # + buffer), so seed them last.
            self._seed_prediction_rounds(tournament, config)
        self.stdout.write(self.style.SUCCESS("Seed complete."))

    # ---- Tournament + Stages (from JSON) ----

    def _seed_tournament_and_stages(self) -> tuple[Tournament, dict]:
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

        for sd in config["stages"]:
            # `defaults` is applied on UPDATE (every deploy); `create_defaults`
            # only on first creation. Ganyan pools live in create_defaults ONLY,
            # so once a Stage exists, admin owns its pool sizes and deploys never
            # clobber them. order + legacy points stay in defaults (kept in sync).
            sync_fields = {
                "order": sd["order"],
                "points_exact": sd["points_exact"],
                "points_diff": sd["points_diff"],
                "points_result": sd["points_result"],
                "penalty_loser_pct": Decimal(sd["penalty_loser_pct"]),
            }
            pool_fields = {
                "pool_exact": sd.get("pool_exact", 100),
                "pool_diff": sd.get("pool_diff", 100),
                "pool_result": sd.get("pool_result", 100),
                "pool_penalty_winner": sd.get("pool_penalty_winner", 25),
                "pool_penalty_score": sd.get("pool_penalty_score", 25),
                "pool_penalty_diff": sd.get("pool_penalty_diff", 25),
                "pool_advancer": sd.get("pool_advancer", 25),
            }
            stage, created = Stage.objects.update_or_create(
                tournament=tournament,
                kind=sd["kind"],
                defaults=sync_fields,
                create_defaults={**sync_fields, **pool_fields},
            )
            self._log(f"  Stage: {stage.get_kind_display()}", created)

        return tournament, config

    # ---- PredictionRounds (after slots, so opens_at can be computed) ----

    def _seed_prediction_rounds(self, tournament: Tournament, config: dict) -> None:
        """Create missing rounds; never touch existing ones.

        Rounds are admin-owned once created: during the live tournament the
        admin closes stages mid-round (e.g. GROUP removed from "Pre-turnuva"
        at kickoff) and extends deadlines. Re-syncing from JSON on deploy
        would silently revert that state — so the seed only fills gaps, and
        never deletes rounds (a delete would cascade into SlotPredictions).
        """
        stage_by_kind = {s.kind: s for s in tournament.stages.all()}

        for rd in config["prediction_rounds"]:
            depends_kind = rd.get("depends_on_stage")
            depends_stage = stage_by_kind.get(depends_kind) if depends_kind else None
            editable_stages = [stage_by_kind[k] for k in rd["editable_stages"] if k in stage_by_kind]

            opens_at = self._compute_opens_at(tournament, depends_stage)
            deadline = self._compute_deadline(
                tournament, editable_stages,
                rd.get("deadline_iso"), tournament_start=config["tournament"]["start_date"],
            )

            round_obj, created = PredictionRound.objects.get_or_create(
                tournament=tournament,
                order=rd["order"],
                defaults={
                    "name": rd["name"],
                    "deadline": deadline,
                    "weight": Decimal(rd["weight"]),
                    "depends_on_stage": depends_stage,
                    "opens_at": opens_at,
                },
            )
            if created:
                round_obj.editable_stages.set(editable_stages)
            self._log(f"  Round: {round_obj.name} (×{round_obj.weight})", created)

    def _compute_opens_at(self, tournament: Tournament, depends_stage: Stage | None):
        """Last kickoff in the source stage, plus a buffer for ET/penalties."""
        if depends_stage is None:
            return None
        last_kickoff = (
            BracketSlot.objects
            .filter(tournament=tournament, stage=depends_stage)
            .aggregate(latest=Max("scheduled_kickoff"))["latest"]
        )
        if last_kickoff is None:
            return None
        buffer_min = _STAGE_BUFFER_MINUTES.get(depends_stage.kind, 180)
        return last_kickoff + timedelta(minutes=buffer_min)

    def _compute_deadline(
        self, tournament: Tournament, editable_stages: list[Stage],
        manual_iso: str | None, tournament_start: str,
    ):
        """The earliest kickoff among editable stages.

        If a manual_iso is provided in JSON it overrides the auto-computed value.
        Falls back to tournament start - 1 day if no slots are seeded yet.
        """
        if manual_iso:
            return datetime.fromisoformat(manual_iso)

        stage_ids = [s.id for s in editable_stages]
        if not stage_ids:
            return datetime.fromisoformat(f"{tournament_start}T00:00:00+00:00") - timedelta(days=1)

        first_kickoff = (
            BracketSlot.objects
            .filter(tournament=tournament, stage_id__in=stage_ids)
            .aggregate(earliest=Min("scheduled_kickoff"))["earliest"]
        )
        if first_kickoff is None:
            return datetime.fromisoformat(f"{tournament_start}T00:00:00+00:00") - timedelta(days=1)
        return first_kickoff

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

            home_grp = _parse_group_source(row.get("home_source", "")) if home_src is None else {}
            away_grp = _parse_group_source(row.get("away_source", "")) if away_src is None else {}

            BracketSlot.objects.filter(pk=slot.pk).update(
                home_source_slot=home_src,
                home_source_kind=home_kind,
                away_source_slot=away_src,
                away_source_kind=away_kind,
                home_source_group_letter=home_grp.get("group_letter", ""),
                home_source_group_position=home_grp.get("group_position"),
                home_source_thirds_groups=home_grp.get("thirds_groups", ""),
                away_source_group_letter=away_grp.get("group_letter", ""),
                away_source_group_position=away_grp.get("group_position"),
                away_source_thirds_groups=away_grp.get("thirds_groups", ""),
            )

    # ---- Helpers ----

    def _log(self, label: str, created: bool) -> None:
        verb = "created" if created else "updated"
        self.stdout.write(f"{label} ({verb})")
