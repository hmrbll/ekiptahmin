# Ganyan Scoring (Parimutuel Pool Model)

**Status:** Shipped 2026-05-18 (engine + cache + signals + public views). Active scoring system on `ekiptahmin.com`.
**Replaces:** The legacy bracket scoring (`apps/scoring/engine.py` + `SlotScore`). Legacy stays alive in parallel under `/legacy/*` staff-only routes for calibration and reference.

## Concept

Each match has a fixed point pool per criterion (default 100 each). The pool is split equally among everyone who got that criterion right — the more people who made the same correct prediction, the smaller each share. Single-prediction wins pay the full pool; consensus predictions pay a thin slice.

This is the parimutuel ganyan model from horse racing applied to football predictions. The score for a match is no longer a fixed amount; it is a function of what the rest of the players predicted.

## Core formula

For a played match `M`, for each criterion `c ∈ {exact, diff, result, penalty_winner, penalty_score, penalty_diff}`:

```
pool_c        = Stage(M).pool_<c>          # admin-tunable, 100 regulation / 25 penalty
N             = unique users who predicted M in any round
W_c           = unique users whose at-least-one round prediction satisfies c
base_payout_c = pool_c / |W_c|             # if |W_c| == 0 the pool burns
```

For each user `U` who predicted `M`, pick the **effective round** `R*` against
the **nominal** pools (each criterion at its full `pool_c`, before any dilution):

```
nominal_UMR  = Σ_c [ sat(pred_UMR, c) × pool_c ]          # full pools, not base_payout
R*           = argmax over rounds R of (nominal_UMR × round_weight_R)   # ties → on-fixture, then earliest R
score(U, M)  = Σ_c [ sat(pred_UMR*, c) × base_payout_c ] × round_weight_R*
```

`R*` is chosen against the full pools, so it depends only on `U`'s **own**
predictions — never on how the crowd splits the pools. Scoring then applies the
diluted `base_payout_c`. All criteria are taken from that single effective round.

**Tie-break:** equal weighted totals fall to the round whose pick is **on the
actual fixture** (strict home AND away), then to the earliest round. This only
bites when every candidate scores 0 — a user who predicted *this* match but
missed ties at 0 with any earlier off-fixture bracket pick. Preferring the
on-fixture pick keeps that user attributed to the match they actually predicted,
so they count toward `N`, appear in the breakdown, and render as a "miss" in the
match/result/home views (which all filter on `R*`'s matchup) instead of vanishing
behind an unrelated bracket pick. It never changes points: an off-fixture pick
scores 0, so the swap is only ever between two 0-point rounds.

### Effective-round selection is decoupled (no fixed-point)

Choosing `R*` against the diluted `base_payout_c` (the old rule) was circular:
`base_payout_c` depends on `|W_c|`, which depends on everyone's effective rounds,
which depends on `base_payout_c`. That needed a fixed-point iteration and could
land on different equilibria depending on the starting guess. Selecting against
the **nominal** pools removes the dependency entirely — every user's pick is a
pure function of their own predictions, so the whole slot resolves in **one
deterministic pass with a unique result**.

The trade: in the rare "flip" case — a later, much-lighter-weight round holding a
**higher tier** than an earlier heavier round — the diluted payouts could have
made that later round pay the user marginally more. Under the nominal rule the
user is scored on their nominal-best round instead, slightly under that
theoretical max. This is only reachable when a user predicted the same match
across rounds whose weights differ by more than ~⅓ (i.e. QF/SF/Final matches at
the current weight schedule); group/R32/R16 matches never reach it. The
deterministic, one-pass, explainable outcome is the deliberate choice. Pinned by
`apps/scoring/tests/test_ganyan_effective_round.py`.

### Example

Result: 1-0. Stage pool = (exact=100, diff=100, result=100).

| User | Pre (w=1.0) | Grup-sonra (w=0.8) |
|------|-------------|--------------------|
| A    | 2-1         | 3-0                |
| B    | —           | 2-1                |
| C    | 1-0         | 1-0                |

Effective round per user (nominal pools = 100 each):

- A: Pre 2-1 → {diff,result} → nominal (100+100)×1.0 = **200**; Grup 3-0 → {result} → 100×0.8 = 80 → **Pre**
- B: only Grup 2-1 → **Grup**
- C: Pre 1-0 → {exact,diff,result} → 300×1.0 = **300**; Grup → 300×0.8 = 240 → **Pre**

Winner counts from those effective picks:

- N = 3 (A, B, C predicted M)
- Exact winners: {C} → base = 100/1 = 100
- Diff winners: {A, B, C} → base = 100/3 = 33.33
- Result winners: {A, B, C} → base = 100/3 = 33.33

Match scores:

| User | R*   | base payouts (exact/diff/result) | × weight | Match score |
|------|------|----------------------------------|----------|-------------|
| A    | Pre  | (0, 33.33, 33.33) = 66.67        | × 1.0    | **66.67**   |
| B    | Grup | (0, 33.33, 33.33) = 66.67        | × 0.8    | **53.33**   |
| C    | Pre  | (100, 33.33, 33.33) = 166.67     | × 1.0    | **166.67**  |

### Burn condition

If no user satisfies criterion `c`, `|W_c| = 0` and the pool burns (no one is paid from it). The pool does **not** roll over.

## Penalty pools (knockout only)

When a KO match goes to penalties (`ActualResult.went_to_penalties = True`), three extra criteria are scored **on top of** the regulation ones (which still score the 90' scoreline). Each is its own pool, default 25, split equally among its winners — same formula and burn rule as the regulation pools.

| Criterion | Wins when… | Open to |
|-----------|------------|---------|
| `penalty_winner` | named the team that advanced via penalties | **any** prediction — implied winner from a non-draw, or the `penalty_winner` derived from the shootout score on a draw (the user never picks it; `SlotPrediction.clean()` sets it from the never-tied shootout score) |
| `penalty_score` | predicted the exact shootout score (e.g. 4–2) | **draw predictions only** (only they carry a shootout score) |
| `penalty_diff` | predicted the shootout goal difference, signed home−away | **draw predictions only** |

All three require the predicted matchup (home/away teams) to line up with the actual one. The headline `outcome` badge collapses the three into a single `penalty` tier, shown only when the user earned from a penalty criterion but missed all three regulation tiers; `GanyanScore.score_penalty` stores the combined payout, while `MatchPool` keeps the per-criterion split for the match-detail tablosu.

This replaces the legacy `penalty_loser_pct = 0.60` mechanic with a pool-based one.

## Extra time (knockout): 120' vs 90'

The regulation criteria (exact / diff / result) judge the **120' score when a
knockout match went to extra time**, otherwise the 90' score. A match won in
extra time is a draw at 90', so judging on 90' would deny credit to everyone who
called the decisive scoreline — judging on 120' rewards them. Penalty criteria
are unaffected (a shootout match is a draw at both 90' and 120').

Group matches never go to extra time, so this collapses to "always 90'" there.
The single source of truth is `ActualResult.effective_home_score` /
`effective_away_score` (120' when `went_to_extra_time` and the `*_score_aet`
fields are set, else 90'); the ganyan bridge and the result-display templates
both read it. The legacy engine stays on the original 90'-only basis as a frozen
reference. The 120' score is captured automatically by the live sync; see
[live-results.md](live-results.md).

## Tiebreaker chain

For the leaderboard, sort by (descending unless noted):

1. Total points
2. Exact-score hit count (weighted by `round_weight_R*`)
3. Diff hit count (weighted)
4. Result hit count (weighted)
5. **Wrong-prediction count** — *ascending* (fewer 0-point predicted matches up top). Only matches the user predicted are counted; missing predictions do not count as "wrong".

Note that layers 2–4 are *weighted* by the effective round's weight, so the same number of correct calls made in earlier (higher-weight) rounds already ranks ahead — that's where early-round correctness is rewarded, not via a clock-time layer.

Users equal on all five layers **share a rank**. The display order among them is alphabetical by nickname — a stable, meaning-free fallback. (There is no clock-time "who submitted first" tiebreaker.)

The tie notes rendered under the leaderboard are always concrete: they name the decisive criterion and each tied user's value on it (e.g. "sırayı ağırlıklı tam skor sayısı kriteri belirledi: Ali 3,40 · Veli 2,55"); for full ties they list the compared criteria and state that the rank is shared. Vague phrasings ("resolved during the tournament") are deliberately avoided.

## Leaderboard display

The board is **tabbed by round**: a **"Genel"** tab (the overall standings, always the default) followed by one tab per round section — the same sections the all-predictions and results pages tab by (Grup İlk/İkinci/Üçüncü Maçlar, then Son 32 → Final; shared `_round_tabs.html` partial, `apps/tournament/sections.py` grouping). Each round tab is a full standalone board over **that round's matches only**: its own totals, stat columns, ranks (full tiebreaker chain restricted to the round) and tie notes. A round appears as a tab only once at least one of its matches is scored (`leaderboard_sections_for_tournament`); users appear in a round tab only if they have score rows there. While "Genel" is the only tab, the tab bar is omitted entirely — the page looks like the plain overall board.

The stat columns (Doğru Skor / Doğru Fark / Doğru Sonuç) show **cumulative** counts: an exact-score hit also counts as a correct goal difference and a correct result, a diff hit also counts as a correct result — same semantics as the weighted tiebreaker layers. "Penaltı" stays a best-tier bucket (earned from a penalty pool while missing all regulation tiers) and "Yanlış" is the wrong-prediction count.

An **Adet/Puan toggle** (client-side, persisted in localStorage) switches every stat cell — across all tabs at once — between hit counts and points earned per criterion (`GanyanScore.score_exact/diff/result/penalty` sums — also cumulative by construction, since an exact hit wins all three regulation pools). "Yanlış" shows the count in both modes.

## All-predictions page — pre-result "best case"

The public all-predictions page (`/predictions/all/`) is organized into **round tabs** rather than one long scroll: the group stage splits by matchday (**Grup İlk / İkinci / Üçüncü Maçlar** — derived from the `GroupX-Mn` suffix, M1-2/3-4/5-6 → matchday 1/2/3), followed by one tab per knockout stage (Son 32 → Final). Tabs are switched client-side; the open tab is mirrored in the URL hash and otherwise defaults to the earliest round that still has an unplayed match (the round "in progress"). Tabs appear only for stages that have at least one match with both teams resolved.

The grouping logic lives in `apps/tournament/sections.py` (`group_matches_into_sections`) and the tab bar + toggle script in the shared `templates/_round_tabs.html` partial. The public **results log** (`/results/`) reuses both: same round tabs, matches ordered chronologically within each tab, and each match's per-player score list collapsed into a `<details>` (**"Oyuncu puanları (N)"**). Since every result is by definition played, its default tab is the last (most advanced) round.

Once a match's predictions are revealed but **before** a result is entered, each pick shows the most it could still earn — labelled **"en fazla N puan"**. After the result is entered this is replaced by the actual earned `GanyanScore.total`, shown on the round that earned it (see below).

The best case is the parimutuel payout the pick would earn **if the match ended exactly as predicted** (`ganyan.potential_max_scores`): the pick wins every criterion its scoreline satisfies (a draw-on-KO pick carrying a shootout wins the three penalty pools too), and each pool is split among everyone whose own revealed pick would also win it under that hypothetical. So it differentiates picks the way real ganyan does — a lone scoreline shows the full pool, a popular one a thin slice.

The card lists **only picks on the slot's actual fixture** — both predicted teams must line up with the resolved matchup (the engine's `_matchup_correct` rule, strict home AND away). In a knockout each player predicted their own bracket, so most of a slot's stored predictions are for a *different* matchup (different teams reaching here); those can never score it and are filtered out of the card entirely rather than listed with a zero. When a slot was predicted but nobody hit the matchup, the card shows **"N oyuncu tahmin etti, ama kimse bu eşleşmeyi tutturamadı"** in place of a prediction list. For group matches the fixture is fixed, so the filter is a no-op.

Because every listed pick is therefore "complete", the **"en fazla N puan"** hint is shown for all of them.

Each pick is its own row, tagged with a **round-weight badge** — e.g. `(0,85x)`, the `PredictionRound.weight` of the round it came from. A player who predicted the same fixture in several rounds gets **one row per round** (earliest first), so you can see how their call and its weight changed; the engine still only scores one of them. The pre-result **"en fazla"** is then computed per row (`ganyan.potential_max_scores_multi`): each pick's best case if the match ends exactly as it calls it, with co-winner denominators counting distinct *users* — a player's two picks never take two slices of the same pool, mirroring the live engine's one-effective-round-per-user rule. Once the result is in, the earned `GanyanScore.total` sits on the **effective round's** row only (`GanyanScore.effective_round`); the player's other rounds show the pick and its weight but no points, since they didn't count. Values are upper bounds in the common single-round case; the live engine can only pay *more*, and only when a co-winner's effective round turns out to sit in a different round.

## Data model

### Modified

- **`Stage`** — ganyan pool fields:
  - `pool_exact` (default 100)
  - `pool_diff` (default 100)
  - `pool_result` (default 100)
  - `pool_penalty_winner`, `pool_penalty_score`, `pool_penalty_diff` (default 25 each, only used on KO stages that go to penalties)
  - Legacy fields (`points_exact`, `points_diff`, `points_result`, `penalty_loser_pct`) stay — used by the legacy engine.
  - **Pool sizes are admin-owned.** `seed_wc2026` writes them only on first Stage creation (`create_defaults`); deploys never re-sync them, so a value edited in Stage admin persists. All stages currently use the uniform 100/100/100 + 25/25/25 scheme (equalized to 100/100/100 + 50/50/50 in `tournament/0009_equalize_ganyan_pools`, penalty pools then lowered 50→25 in `tournament/0012`; both only retune rows still on the old default).

### New

- **`GanyanScore`** (in `apps/scoring/`)
  - `user`, `slot` (FK to BracketSlot)
  - `score` (Decimal)
  - `score_exact`, `score_diff`, `score_result`, `score_penalty` (per-criterion breakdown)
  - `effective_round` (FK to PredictionRound)
  - `effective_round_score_unweighted` (sum of criterion payouts before weight) — surfaced in UI
  - `wrong_count_contribution` (0 or 1) — drives tiebreaker layer 5
  - `updated_at`
  - Unique on (user, slot).

- **`MatchPool`** (in `apps/scoring/`)
  - `slot` (FK)
  - `criterion` (exact / diff / result / penalty_winner / penalty_score / penalty_diff)
  - `pool_size` (snapshot of `Stage.pool_<criterion>` at compute time)
  - `winner_count` (|W_c|)
  - `base_payout` (`pool_size / winner_count` or null if burned)
  - `predictor_count` (N — users whose effective pick is on the actual fixture; wrong-matchup picks from a different bracket are excluded, matching the breakdown)
  - `breakdown` (JSON: `{prediction_value: count}` for the ganyan tablosu UI)
  - `computed_at`
  - Unique on (slot, criterion).

Both tables are materialized caches, rebuilt on `ActualResult` write via signals (same pattern as legacy `SlotScore`).

## Compute trigger

Single signal handler on `ActualResult` post-save:

```
1. Run legacy engine → write SlotScore rows  (existing behavior, unchanged)
2. Run ganyan engine → write GanyanScore + MatchPool rows
3. Invalidate leaderboard caches for affected users
```

`MatchPool` rows are also recomputed on `SlotPrediction` write **after lock**, so the ganyan tablosu stays accurate if a prediction is corrected by an admin post-lock. (Pre-lock predictions don't trigger.) **Reveal gates differ by surface:** the match-detail tablosu (incl. its pre-result pool preview) and the per-user prediction list reveal once the slot's **prediction round has closed** — every round that could still edit the slot's stage has passed its deadline, so the pick is final — **or** the slot is scored (`BracketSlot.predictions_round_closed or actual is not None`, in `apps/scoring/views` and the staff-only `/legacy/*` user-detail). This trips at the stage's round deadline, which for later matches in a stage is *earlier* than their own kickoff (a stage's picks all surface together when its round closes). The **home-grid prediction chips** are stricter: they reveal only **once the result is entered** (`actual is not None`, in `config/views._chips_for_slots`), because they're colour-coded by `GanyanScore.outcome`, which doesn't exist until the slot is scored. They also apply the same **strict-matchup filter** as the tablosu — on a knockout slot only chips whose effective pick names the real teams are shown; wrong-matchup picks from a different bracket are dropped rather than surfaced as a bare score (a no-op for group slots, where the fixture is fixed). The per-match **player lists** (`Oyuncu Puanları` on `/results/` and `/matches/<slot_id>/`) apply this same filter on each user's effective pick, so the list count equals the tablosu's `predictor_count` N. When every pick on a knockout slot was off-fixture, `/results/` shows a `"N oyuncu tahmin etti, ama kimse bu eşleşmeyi tutturamadı"` note instead of the player rows (mirroring the all-predictions card).

## URLs

| Path | Audience | Source |
|------|----------|--------|
| `/` | Public | GanyanScore + new tiebreaker |
| `/matches/<slot_id>/` (new) | Public | Match detail + ganyan tablosu (post round-close) |
| `/legacy/leaderboard/` | `staff_member_required` | SlotScore + legacy tiebreaker |
| `/legacy/results/` | `staff_member_required` | Existing results view, re-routed |
| `/legacy/scoring-diff/` | `staff_member_required` | Side-by-side: SlotScore vs GanyanScore per user |

## Configuration scope

All knobs admin-tunable, no Python constants:

- `Stage.pool_*` (per stage, per criterion) — pool sizes
- `PredictionRound.weight` (existing) — round weight multiplier
- Defaults seeded by `seed_wc2026`; admin can override per stage.

## Simulation

The existing simulation requirement ([[feedback_scoring_config]]) applies here too. A staff-only simulation page should let Hemre:
- Load historical predictions (or hypothetical inputs)
- Adjust `Stage.pool_*` values
- See the resulting leaderboard without writing to the live DB

Implementation deferred to a later phase but the data model must support it (compute engine takes parameters, doesn't read globals).

## Migration / cutover

- Existing `SlotScore` rows stay untouched (legacy continues to recompute).
- For matches that already have `ActualResult` rows at migration time, a one-shot management command (`recompute_ganyan`) populates `GanyanScore` + `MatchPool`.
- `build.sh` runs `recompute_ganyan` on every deploy as an idempotent safety net — covers results entered before the engine shipped and any slot whose `post_save` signal was missed.
- WC2026 hasn't kicked off yet (May 2026), so the cutover affects test/seed data only.

## Open items (deferred)

- Simulation UI (staff-only page).
- KO penalty pool detail — revisit when KO stage approaches (June 2026).
- "Live ganyan" while match is in progress — depends on Faz 2.2 (live match display).
