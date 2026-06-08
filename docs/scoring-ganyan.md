# Ganyan Scoring (Parimutuel Pool Model)

**Status:** Shipped 2026-05-18 (engine + cache + signals + public views). Active scoring system on `ekiptahmin.com`.
**Replaces:** The legacy bracket scoring (`apps/scoring/engine.py` + `SlotScore`). Legacy stays alive in parallel under `/legacy/*` staff-only routes for calibration and reference.

## Concept

Each match has a fixed point pool per criterion (default 100 each). The pool is split equally among everyone who got that criterion right — the more people who made the same correct prediction, the smaller each share. Single-prediction wins pay the full pool; consensus predictions pay a thin slice.

This is the parimutuel ganyan model from horse racing applied to football predictions. The score for a match is no longer a fixed amount; it is a function of what the rest of the players predicted.

## Core formula

For a played match `M`, for each criterion `c ∈ {exact, diff, result, penalty_winner, penalty_score, penalty_diff}`:

```
pool_c        = Stage(M).pool_<c>          # admin-tunable, 100 regulation / 50 penalty
N             = unique users who predicted M in any round
W_c           = unique users whose at-least-one round prediction satisfies c
base_payout_c = pool_c / |W_c|             # if |W_c| == 0 the pool burns
```

For each user `U` who predicted `M`, pick the **effective round** `R*`:

```
score_UMR    = Σ_c [ sat(pred_UMR, c) × base_payout_c ]
R*           = argmax over rounds R of (score_UMR × round_weight_R)
score(U, M)  = score_UMR* × round_weight_R*
```

Tüm kriterler aynı turdan alınır (single effective round per user-match) — bu mevcut sistemin "max-puan-turu" mantığıyla uyumludur.

### Example

Result: 1-0. Stage pool = (exact=100, diff=100, result=100).

| User | Pre (w=1.0) | Grup-sonra (w=0.8) |
|------|-------------|--------------------|
| A    | 2-1         | 3-0                |
| B    | —           | 2-1                |
| C    | 1-0         | 1-0                |

- N = 3 (A, B, C predicted M somewhere)
- Exact winners: {C} → base = 100/1 = 100
- Diff winners: {A (Pre), B (Grup-sonra), C} → base = 100/3 = 33.33
- Result winners: {A, B, C} → base = 100/3 = 33.33

Per-user effective round and score:

| User | Round | sat(exact/diff/result) | Round score | × weight | Match score |
|------|-------|------------------------|-------------|----------|-------------|
| A    | Pre   | (0, 0, 33.33)          | 33.33       | × 1.0    | **33.33**   |
| A    | Grup  | (0, 0, 33.33)          | 33.33       | × 0.8    | 26.67       |
| B    | Grup  | (0, 33.33, 33.33)      | 66.67       | × 0.8    | **53.33**   |
| C    | Pre   | (100, 33.33, 33.33)    | 166.67      | × 1.0    | **166.67**  |
| C    | Grup  | (100, 33.33, 33.33)    | 166.67      | × 0.8    | 133.33      |

Highlighted row per user is the effective round.

### Burn condition

If no user satisfies criterion `c`, `|W_c| = 0` and the pool burns (no one is paid from it). The pool does **not** roll over.

## Penalty pools (knockout only)

When a KO match goes to penalties (`ActualResult.went_to_penalties = True`), three extra criteria are scored **on top of** the regulation ones (which still score the 90' scoreline). Each is its own pool, default 50, split equally among its winners — same formula and burn rule as the regulation pools.

| Criterion | Wins when… | Open to |
|-----------|------------|---------|
| `penalty_winner` | named the team that advanced via penalties | **any** prediction — implied winner from a non-draw, or the chosen `penalty_winner` from a draw |
| `penalty_score` | predicted the exact shootout score (e.g. 4–2) | **draw predictions only** (only they carry a shootout score) |
| `penalty_diff` | predicted the shootout goal difference, signed home−away | **draw predictions only** |

All three require the predicted matchup (home/away teams) to line up with the actual one. The headline `outcome` badge collapses the three into a single `penalty` tier, shown only when the user earned from a penalty criterion but missed all three regulation tiers; `GanyanScore.score_penalty` stores the combined payout, while `MatchPool` keeps the per-criterion split for the match-detail tablosu.

This replaces the legacy `penalty_loser_pct = 0.60` mechanic with a pool-based one.

## Tiebreaker chain

For the leaderboard, sort by (descending unless noted):

1. Total points
2. Exact-score hit count (weighted by `round_weight_R*`)
3. Diff hit count (weighted)
4. Result hit count (weighted)
5. **Wrong-prediction count** — *ascending* (fewer 0-point predicted matches up top). Only matches the user predicted are counted; missing predictions do not count as "wrong".

Note that layers 2–4 are *weighted* by the effective round's weight, so the same number of correct calls made in earlier (higher-weight) rounds already ranks ahead — that's where early-round correctness is rewarded, not via a clock-time layer.

Users equal on all five layers **share a rank**. The display order among them is alphabetical by nickname — a stable, meaning-free fallback; a real tie is resolved manually during the tournament if it ever matters. (There is no clock-time "who submitted first" tiebreaker.)

## Data model

### Modified

- **`Stage`** — ganyan pool fields:
  - `pool_exact` (default 100)
  - `pool_diff` (default 100)
  - `pool_result` (default 100)
  - `pool_penalty_winner`, `pool_penalty_score`, `pool_penalty_diff` (default 50 each, only used on KO stages that go to penalties)
  - Legacy fields (`points_exact`, `points_diff`, `points_result`, `penalty_loser_pct`) stay — used by the legacy engine.
  - **Pool sizes are admin-owned.** `seed_wc2026` writes them only on first Stage creation (`create_defaults`); deploys never re-sync them, so a value edited in Stage admin persists. All stages currently use the uniform 100/100/100 + 50/50/50 scheme (set once by migration `tournament/0009_equalize_ganyan_pools`).

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
  - `predictor_count` (N — total unique predictors for slot)
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

`MatchPool` rows are also recomputed on `SlotPrediction` write **after lock**, so the ganyan tablosu UI stays accurate if a prediction is corrected by an admin post-lock. (Pre-lock predictions don't trigger; the tablosu only shows post-lock.)

## URLs

| Path | Audience | Source |
|------|----------|--------|
| `/` | Public | GanyanScore + new tiebreaker |
| `/matches/<slot_id>/` (new) | Public | Match detail + ganyan tablosu (post-lock) |
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
