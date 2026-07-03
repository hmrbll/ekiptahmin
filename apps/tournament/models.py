from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class Tournament(models.Model):
    """A single tournament container (e.g., FIFA World Cup 2026).

    Designed for multi-tournament reuse: each tournament has its own teams,
    stages, prediction rounds, and bracket slots.
    """

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(
        default=True,
        help_text="Only one active tournament should exist at a time (drives default UI selections).",
    )
    timezone = models.CharField(
        max_length=64,
        default="Europe/Istanbul",
        help_text="Default display TZ for anonymous visitors. Authenticated users use their own.",
    )

    class Meta:
        ordering = ("-start_date",)

    def __str__(self) -> str:
        return self.name


class Stage(models.Model):
    """A stage of the tournament (Group, R32, R16, QF, SF, Third Place, Final).

    Scoring parameters live here — admin-tunable, never hardcoded in Python.
    Different tournaments can have different stage layouts (2022 had no R32).
    """

    GROUP = "GROUP"
    R32 = "R32"
    R16 = "R16"
    QF = "QF"
    SF = "SF"
    THIRD = "THIRD"
    FINAL = "FINAL"
    KIND_CHOICES = [
        (GROUP, "Group Stage"),
        (R32, "Round of 32"),
        (R16, "Round of 16"),
        (QF, "Quarter Final"),
        (SF, "Semi Final"),
        (THIRD, "Third Place Match"),
        (FINAL, "Final"),
    ]

    # Turkish UI label per stage. `get_kind_display` keeps the English choice
    # label for the Django admin; user-facing templates use `kind_label_tr`.
    # (Convention: UI in TR, admin/code in EN.)
    KIND_LABELS_TR = {
        GROUP: "Grup aşaması",
        R32: "Son 32",
        R16: "Son 16",
        QF: "Çeyrek final",
        SF: "Yarı final",
        THIRD: "Üçüncülük maçı",
        FINAL: "Final",
    }

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="stages")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    order = models.PositiveSmallIntegerField(help_text="0 = Group, 6 = Final (defines progression)")

    # Legacy bracket-scoring fields — drive apps/scoring/engine.py (SlotScore).
    # Public site uses the ganyan engine and reads pool_* fields below.
    # Legacy stays alive for staff-only /legacy/* views.
    points_exact = models.PositiveSmallIntegerField(
        help_text="LEGACY engine only. Points awarded when the predicted score exactly matches the actual score.",
    )
    points_diff = models.PositiveSmallIntegerField(
        help_text="LEGACY engine only. Points for correct outcome AND correct goal difference (but wrong exact score).",
    )
    points_result = models.PositiveSmallIntegerField(
        help_text="LEGACY engine only. Points for correct outcome only (winner or draw).",
    )
    penalty_loser_pct = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal("0.60"),
        help_text="LEGACY engine only. When a user did NOT predict a draw but correctly named the team that "
                  "advanced through penalties: percentage of `points_result` they receive "
                  "(rounded to nearest integer, then multiplied by round weight).",
    )

    # Ganyan pool sizes (parimutuel scoring). Each match has a fixed pool per
    # criterion; the pool is split equally among users who got that criterion
    # right. If no one got it right, the pool burns. See docs/scoring-ganyan.md.
    #
    # These are admin-tunable and are NOT re-synced from the seed file on deploy
    # (seed_wc2026 writes them only on first Stage creation, via create_defaults),
    # so a value edited in admin persists across deploys.
    pool_exact = models.PositiveIntegerField(
        default=100,
        help_text="Ganyan pool size for exact-score winners. Split equally; burns if no one is correct.",
    )
    pool_diff = models.PositiveIntegerField(
        default=100,
        help_text="Ganyan pool size for goal-difference winners.",
    )
    pool_result = models.PositiveIntegerField(
        default=100,
        help_text="Ganyan pool size for outcome (1X2) winners.",
    )
    # Penalty shootout pools — only relevant on knockout stages that go to
    # penalties. Three parallel criteria mirroring the regulation ones:
    pool_penalty_winner = models.PositiveIntegerField(
        default=25,
        help_text="Ganyan pool: correctly named the team that advanced via penalties. "
                  "Open to any prediction (implied winner from a non-draw, chosen penalty "
                  "winner from a draw). Knockout only.",
    )
    pool_penalty_score = models.PositiveIntegerField(
        default=25,
        help_text="Ganyan pool: predicted the exact penalty shootout score. Only draw "
                  "predictions carry a shootout score, so this is open to them only. Knockout only.",
    )
    pool_penalty_diff = models.PositiveIntegerField(
        default=25,
        help_text="Ganyan pool: predicted the penalty shootout goal difference (signed home−away). "
                  "Draw predictions only. Knockout only.",
    )

    class Meta:
        ordering = ("tournament", "order")
        unique_together = (("tournament", "kind"),)

    @property
    def kind_label_tr(self) -> str:
        """Turkish UI label for this stage (admin keeps the English display)."""
        return self.KIND_LABELS_TR.get(self.kind, self.get_kind_display())

    def __str__(self) -> str:
        return f"{self.get_kind_display()} ({self.tournament.slug})"


class Team(models.Model):
    """A national team participating in a specific tournament.

    Teams are tournament-scoped because group assignments and participant
    rosters change between tournaments.
    """

    # FIFA 3-letter code -> ISO alpha-2 (or flag-icons subdivision code).
    # Drives the SVG flag lookup at static/flags/<iso>.svg.
    FIFA_TO_ISO = {
        "TUR": "tr", "USA": "us", "MEX": "mx", "KOR": "kr", "RSA": "za", "CZE": "cz",
        "BIH": "ba", "CAN": "ca", "QAT": "qa", "SUI": "ch", "BRA": "br", "HAI": "ht",
        "MAR": "ma", "SCO": "gb-sct", "AUS": "au", "PAR": "py", "CUW": "cw", "ECU": "ec",
        "GER": "de", "CIV": "ci", "JPN": "jp", "NED": "nl", "SWE": "se", "TUN": "tn",
        "BEL": "be", "EGY": "eg", "IRN": "ir", "NZL": "nz", "CPV": "cv", "KSA": "sa",
        "ESP": "es", "URU": "uy", "FRA": "fr", "IRQ": "iq", "NOR": "no", "SEN": "sn",
        "ALG": "dz", "ARG": "ar", "AUT": "at", "JOR": "jo", "COL": "co", "COD": "cd",
        "POR": "pt", "UZB": "uz", "CRO": "hr", "ENG": "gb-eng", "GHA": "gh", "PAN": "pa",
    }

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="teams")
    code = models.CharField(max_length=3, help_text="3-letter code (TUR, BRA, ESP).")
    name_tr = models.CharField(max_length=100, help_text="Country name in Turkish (user-facing).")
    flag_emoji = models.CharField(
        max_length=16,
        blank=True,
        help_text="Flag emoji (fallback when SVG fails or in plain-text contexts).",
    )
    group_letter = models.CharField(
        max_length=1,
        blank=True,
        help_text="A-L for the 12 groups in 2026. Empty for teams that didn't reach group stage.",
    )

    class Meta:
        ordering = ("group_letter", "name_tr")
        unique_together = (("tournament", "code"),)

    @property
    def flag_iso(self) -> str:
        """ISO/flag-icons code for the SVG flag (e.g., 'tr', 'gb-eng')."""
        return self.FIFA_TO_ISO.get(self.code.upper(), "")

    @property
    def flag_svg_path(self) -> str:
        """Static-file path for the SVG flag, suitable for `{% static %}` tags."""
        iso = self.flag_iso
        return f"flags/{iso}.svg" if iso else ""

    def __str__(self) -> str:
        return f"{self.flag_emoji} {self.name_tr}".strip()


class PredictionRound(models.Model):
    """One of the prediction rounds (pre-tournament, after group stage, etc.).

    The `weight` field is the multiplier applied to scores from predictions made
    in this round. Earlier rounds carry higher weight to reward bold/early calls.

    Open conditions (all must hold for `is_open` to return True):
    1. now < deadline
    2. opens_at is null OR now >= opens_at
    3. depends_on_stage is null OR every slot in that stage has an actual result
    """

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="prediction_rounds")
    name = models.CharField(max_length=100, help_text="e.g., 'Pre-tournament', 'After Group Stage'.")
    order = models.PositiveSmallIntegerField()
    deadline = models.DateTimeField(help_text="UTC; predictions for this round can no longer be edited after this.")
    opens_at = models.DateTimeField(
        null=True, blank=True,
        help_text="UTC; round stays closed until this moment. Typically auto-set by seed "
                  "to (last source-stage match kickoff + buffer for ET/penalties).",
    )
    depends_on_stage = models.ForeignKey(
        Stage,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="opens_rounds",
        help_text="Round opens only after EVERY slot in this stage has an actual result. "
                  "Pre-tournament rounds leave this null.",
    )
    weight = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        help_text="Score multiplier (1.00 = full points, 0.50 = half points).",
    )
    editable_stages = models.ManyToManyField(
        Stage,
        related_name="editable_in_rounds",
        help_text="Which stages can be predicted/edited in this round.",
    )

    class Meta:
        ordering = ("tournament", "order")
        unique_together = (("tournament", "order"),)

    def __str__(self) -> str:
        return f"{self.name} (×{self.weight})"

    @property
    def is_open(self) -> bool:
        now = timezone.now()
        if now >= self.deadline:
            return False
        if self.opens_at and now < self.opens_at:
            return False
        if self.depends_on_stage_id and self._unresolved_dependency_count() > 0:
            return False
        return True

    @property
    def is_pending_results(self) -> bool:
        """Round has all time conditions met but is waiting on actual results."""
        now = timezone.now()
        if now >= self.deadline:
            return False
        if self.opens_at and now < self.opens_at:
            return False
        return self.depends_on_stage_id is not None and self._unresolved_dependency_count() > 0

    @property
    def is_passed(self) -> bool:
        """Every slot in this round's editable stages has already kicked off.

        Distinguishes "closed for now" (deadline passed but matches still
        upcoming) from "closed for good — the matches this round was about
        have already been played".
        """
        now = timezone.now()
        if now < self.deadline:
            return False
        return not BracketSlot.objects.filter(
            tournament_id=self.tournament_id,
            stage__in=self.editable_stages.all(),
            scheduled_kickoff__gt=now,
        ).exists()

    def _unresolved_dependency_count(self) -> int:
        if not self.depends_on_stage_id:
            return 0
        return (
            BracketSlot.objects
            .filter(tournament_id=self.tournament_id, stage_id=self.depends_on_stage_id)
            .filter(result__isnull=True)
            .count()
        )


class BracketSlot(models.Model):
    """A position in the tournament bracket, identified by a stable string ID.

    Position naming examples:
    - Group: 'GroupA-M1' .. 'GroupL-M6'
    - Knockout: 'R32-1' .. 'R32-16', 'R16-1' .. 'R16-8',
      'QF-1' .. 'QF-4', 'SF-1', 'SF-2', 'Third', 'Final'
    """

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name="slots")
    stage = models.ForeignKey(Stage, on_delete=models.PROTECT, related_name="slots")
    position = models.CharField(
        max_length=30,
        db_index=True,
        help_text="Stable ID, e.g., 'GroupA-M1', 'R32-1', 'QF-3', 'Final', 'Third'.",
    )

    scheduled_kickoff = models.DateTimeField(help_text="UTC; rendered in user's TZ.")
    venue = models.CharField(max_length=100, blank=True)

    # Group matches: teams known at seed time. Knockout: filled in as bracket resolves.
    home_team_actual = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="home_slots",
    )
    away_team_actual = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="away_slots",
    )

    # Free-text source description, used for knockout slots until teams are determined
    # (e.g., 'Group A 1st', 'Winner of R32-1'). Useful for the bracket display.
    home_source = models.CharField(
        max_length=80,
        blank=True,
        help_text="Source description for unresolved slots, e.g., 'Group A 1st' or 'Winner of R32-1'.",
    )
    away_source = models.CharField(max_length=80, blank=True)

    # Structured links for cascade logic. R16/QF/SF/Third/Final slots reference
    # the slot whose winner/loser feeds them. R32 slots leave these null —
    # their teams come from group standings (handled by best-third rules at
    # the actual tournament; users free-pick at prediction time).
    SOURCE_KIND_WINNER = "WINNER"
    SOURCE_KIND_LOSER = "LOSER"
    SOURCE_KIND_CHOICES = [
        (SOURCE_KIND_WINNER, "Winner of source slot"),
        (SOURCE_KIND_LOSER, "Loser of source slot"),
    ]

    home_source_slot = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="feeds_home_to",
        help_text="The slot whose winner/loser feeds the home side of this slot.",
    )
    home_source_kind = models.CharField(
        max_length=8, choices=SOURCE_KIND_CHOICES, default=SOURCE_KIND_WINNER,
    )
    away_source_slot = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="feeds_away_to",
    )
    away_source_kind = models.CharField(
        max_length=8, choices=SOURCE_KIND_CHOICES, default=SOURCE_KIND_WINNER,
    )

    # Group-derived sources (R32 only). Either *_group_letter+*_group_position is set
    # for "A Grubu 2.si" style sources, or *_thirds_groups holds a comma-separated
    # list of letters for "3.lerden biri (A/B/C)" sources. The two are mutually
    # exclusive per side.
    home_source_group_letter = models.CharField(max_length=1, blank=True, default="")
    home_source_group_position = models.PositiveSmallIntegerField(null=True, blank=True)
    home_source_thirds_groups = models.CharField(
        max_length=20, blank=True, default="",
        help_text="Comma-separated group letters for best-third sources (e.g., 'A,B,C,D,F').",
    )
    away_source_group_letter = models.CharField(max_length=1, blank=True, default="")
    away_source_group_position = models.PositiveSmallIntegerField(null=True, blank=True)
    away_source_thirds_groups = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        ordering = ("scheduled_kickoff",)
        unique_together = (("tournament", "position"),)

    def __str__(self) -> str:
        return f"{self.position} — {self.stage.get_kind_display()}"

    @property
    def is_locked(self) -> bool:
        """Predictions for this slot are locked once kickoff has passed."""
        return timezone.now() >= self.scheduled_kickoff

    @property
    def predictions_round_closed(self) -> bool:
        """True once no prediction round can still edit this slot's stage — so the
        pick can no longer change and it's safe to reveal.

        A stage is in a round's ``editable_stages`` only while it's still editable
        there; closing a stage prunes it from every round's ``editable_stages``.
        So the stage is closed when no *open* round (deadline still in the future)
        lists it — whether because every such round's deadline has passed or
        because the stage was pruned entirely.

        This is the reveal gate for the public/legacy score sheets and the
        match-detail tablosu. It trips at the stage's prediction-round deadline,
        which for later matches in a stage is *earlier* than the match's own
        kickoff (so a stage's picks all surface together when its round closes,
        not match-by-match). The home-grid chips use a stricter result-only gate.
        """
        now = timezone.now()
        return not self.stage.editable_in_rounds.filter(deadline__gt=now).exists()

    @property
    def has_cascaded_teams(self) -> bool:
        """True when both team sides come from earlier knockout slot predictions."""
        return bool(self.home_source_slot_id and self.away_source_slot_id)

    @property
    def display_position(self) -> str:
        """Human-friendly TR label, e.g. 'Grup A · İlk Maçlar'.

        Group slots M1..M6 collapse to 3 matchdays (M1,M2 → 1, M3,M4 → 2,
        M5,M6 → 3). Knockout slots keep their position string verbatim
        (R32-3, Final, ...) since those are already short and stable.
        """
        pos = self.position
        if pos.startswith("Group") and "-M" in pos:
            try:
                letter, m_part = pos[len("Group"):].split("-M", 1)
                m = int(m_part)
            except (ValueError, IndexError):
                return pos
            matchday_labels = {1: "İlk Maçlar", 2: "İkinci Maçlar", 3: "Üçüncü Maçlar"}
            matchday = (m - 1) // 2 + 1
            label = matchday_labels.get(matchday)
            if label:
                return f"Grup {letter} · {label}"
        return pos


class ActualResult(models.Model):
    """The actual result for a slot, entered by an admin.

    The 90-minute score is the canonical scoring basis (matches the 2022 group rules).
    Extra time and penalty shootouts are tracked via separate flags so the scoring
    engine can apply the special-case rules from `Stage.penalty_loser_pct`.
    """

    slot = models.OneToOneField(BracketSlot, on_delete=models.CASCADE, related_name="result")
    home_score = models.PositiveSmallIntegerField(help_text="Score at the end of regulation (90').")
    away_score = models.PositiveSmallIntegerField(help_text="Score at the end of regulation (90').")

    went_to_extra_time = models.BooleanField(default=False)
    went_to_penalties = models.BooleanField(default=False)

    # Score after extra time (120'), when played. Scoring stays 90'-based; these
    # exist so the bracket resolver can decide an ET-without-penalties winner
    # (90' is a draw, so home_score/away_score alone can't). Null for matches
    # decided in regulation.
    home_score_aet = models.PositiveSmallIntegerField(null=True, blank=True)
    away_score_aet = models.PositiveSmallIntegerField(null=True, blank=True)

    # Penalty details (only populated when went_to_penalties=True)
    penalty_winner = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="penalty_wins",
    )
    home_penalties = models.PositiveSmallIntegerField(null=True, blank=True)
    away_penalties = models.PositiveSmallIntegerField(null=True, blank=True)

    entered_at = models.DateTimeField(auto_now=True)
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    # Who last wrote this result. A MANUAL row is authoritative: the wizard is
    # the final word, so the live sync never overwrites it (and resync_slots
    # only does with --force). API rows stay poller-owned until edited by hand.
    SOURCE_MANUAL = "MANUAL"
    SOURCE_API = "API"
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Manual entry"),
        (SOURCE_API, "football-data.org live sync"),
    ]
    source = models.CharField(
        max_length=10, choices=SOURCE_CHOICES, default=SOURCE_MANUAL,
    )

    @property
    def went_to_extra_time_with_score(self) -> bool:
        """ET was played AND we recorded the 120' score (so it's usable)."""
        return bool(
            self.went_to_extra_time
            and self.home_score_aet is not None
            and self.away_score_aet is not None
        )

    @property
    def effective_home_score(self) -> int:
        """Scoreline predictions are judged against (and shown as the result):
        the 120' score when the match went to extra time, else the 90' score.
        Knockout-only in practice — group matches never go to ET."""
        return self.home_score_aet if self.went_to_extra_time_with_score else self.home_score

    @property
    def effective_away_score(self) -> int:
        return self.away_score_aet if self.went_to_extra_time_with_score else self.away_score

    def __str__(self) -> str:
        score = f"{self.home_score}-{self.away_score}"
        if self.went_to_penalties and self.penalty_winner:
            score += f" (pen: {self.penalty_winner.code})"
        return f"{self.slot.position}: {score}"
