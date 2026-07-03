# Live results (football-data.org)

Auto-fills match results from [football-data.org](https://www.football-data.org)
during the tournament, so scores no longer have to be typed in by hand. Live
in-play scores show on the homepage and feed the real ganyan scoring — a live
score is treated as the actual result and updated as the match goes on.

## Design at a glance

- **The API only supplies match scores.** Our own code owns the tournament tree
  (who advances) and the scoring mechanic. See *Bracket resolver* below.
- **Visitors never hit the API.** The homepage polls *our* endpoint; the server
  calls football-data at most once per 45s (throttled, single-instance lock),
  and only while a match is in the live window.
- **Auto-write.** A live score is written to `ActualResult` (`source=API`) and
  updated every poll. Once a match is `FINISHED` the slot is `finalized` and
  never requested again.
- **Manual entry wins.** A result saved through the staff wizard is stamped
  `source=MANUAL` and is authoritative: the poller keeps tracking status/minute
  (live badge, finalize) but **never overwrites a manual result**. Reclaiming a
  manual row for the API path takes an explicit `resync_slots <pos> --force`.
- **Homepage stays live.** The `#live-scores` module polls `/live/` every 30s
  (drives the sync); the `#home-grid` module polls `home_grid` every 30s so the
  "Son sonuçlar" column and leaderboard refresh on their own — a finished match
  lands there within ~30s without a manual reload.

```
homepage (HTMX every 30s) → /live/ → maybe_sync_live()  ──throttle/lock──▶ sync_live_matches()
                                                                              │ (only if a slot is in the live window)
                                          football-data /competitions/WC/matches
                                                                              │
                                              map_score → ActualResult (source=API)
                                                                              │ post_save signal
                                                          ganyan recompute + resolve_bracket
```

## Configuration

| Setting | Env var | Default |
|---|---|---|
| API key | `FOOTBALL_DATA_API_KEY` | `""` (disabled) |
| Base URL | `FOOTBALL_DATA_BASE_URL` | `https://api.football-data.org/v4` |
| Competition code | `FOOTBALL_DATA_COMPETITION` | `WC` |

With no key the live sync is a no-op and `fd_probe` refuses to run; nothing else
breaks. Safe to set the key locally to test against real (finished) matches.

## Commands

```bash
# Connectivity probe — read-only, no DB writes. Verify auth, the WC code, the
# score schema, and our team-code mapping.
python manage.py fd_probe --finished            # finished matches + slot mapping
python manage.py fd_probe --finished --raw-score # dump full score JSON

# Map football-data match ids onto BracketSlots (writes MatchSync.external_id).
# Re-runnable; group slots map immediately, knockout slots once teams resolve.
python manage.py map_external_ids [--dry-run]

# One sync pass (the homepage trigger runs the same core automatically).
python manage.py sync_live_results [--dry-run]

# Finalize matches that are over but missed their FINISHED poll (idempotent).
# Runs on the live-sync cron right after each sync pass.
python manage.py finalize_stale_syncs [--dry-run]

# Recompute bracket team assignments from current results (idempotent).
python manage.py resolve_bracket

# Force re-pull specific slots from the API, bypassing the live window AND
# MatchSync.finalized — the repair for rows that finalized with bad data
# (mid-shootout captures, stale scores). Rewrites through the normal sync
# path, so scoring recomputes via the save signal. Idempotent. Manually
# entered rows (source=MANUAL) are authoritative and skipped without --force.
python manage.py resync_slots R32-3 [R16-1 ...] [--dry-run] [--force]
```

## How scores map (`apps/liveresults/score.py`)

Our canonical score is the **90-minute** result; football-data's `score` object
(v4) is translated as:

| `duration` | 90' score (`home/away_score`) | 120' score (`home/away_score_aet`) | flags |
|---|---|---|---|
| `REGULAR` | `fullTime` (also the live running score) | — | — |
| `EXTRA_TIME` | `regularTime` | `fullTime` | `went_to_extra_time` |
| `PENALTY_SHOOTOUT` | `regularTime` (a draw) | `fullTime − penalties` | `went_to_extra_time` + `went_to_penalties`; `penalties`; winner = more shootout goals |

The penalty winner is derived from the shootout score (more goals advances), not
the provider's `winner` field.

> ⚠️ **Penalty quirk:** for a shootout, football-data folds the shootout goals
> into `fullTime` — a 1-1 ET draw won 3-4 on penalties reports `fullTime` 4-5.
> So the clean 120' score is `fullTime − penalties` (a draw), **not** `fullTime`.
> Reading it straight off `fullTime` inflates both the displayed result and the
> exact/diff/result scoring (which judge `effective_*_score`). Rows synced before
> this was fixed are repaired one-off by `python manage.py fix_penalty_aet`
> (idempotent; recomputes ganyan via the save signal). That command only
> recognises the pure-inflation shape (`aet − penalties` must yield a draw) —
> a row whose penalties were later corrected by hand but whose aet stayed stale
> slips through it. For those (and any other stuck-finalized row, e.g. a
> shootout captured mid-round), `python manage.py resync_slots <position>`
> re-pulls the authoritative result from the API.

## Match identity (`apps/liveresults/mapping.py`)

There is no shared id, so a payload is matched to a `BracketSlot` by **team-code
pair + date**. football-data's 3-letter `tla` matched our FIFA `Team.code` 1:1
for WC 2026 (verified live, 0 unmapped), so `TLA_OVERRIDES` is empty — fill it
if a future divergence appears. Knockout slots map only once their teams are
known (their team pair is empty until then).

## Live window & throttle (`apps/liveresults/sync.py`)

A slot is "live" for syncing from **15 min before kickoff until it's
`FINISHED`** (`MatchSync.finalized` — the usual stop). As a fallback when no
FINISHED ever arrives (manual entry / API gap), it also stops once we're past
the match's expected end + 30 min — per stage, since knockouts run longer:
`live_cap` = **140 min** after kickoff for group (≈110' play + 30' grace),
**180 min** for knockout (extra time + penalties + grace). The same cap bounds
the display via `live_syncs()` — the one definition of "currently live" shared
by the homepage "CANLI" module (which renders those rows), the "Son sonuçlar"
list (which excludes their slots), **and** the played-matches log (`/results/`)
+ match-detail ganyan page (`/matches/<id>/`). Because both honour the cap, a
match stuck `IN_PLAY` (FINISHED missed) drops off the live module after its cap
**and** resurfaces in recent results, instead of vanishing from both. `sync_live_matches` returns immediately (no API call)
when nothing is in the window — this is what keeps a forgotten open tab from
polling football-data overnight. `maybe_sync_live` adds a 45s throttle + a cache
lock so concurrent visitors trigger at most one external call. **Assumes a single
web instance** (the throttle is per-process); revisit if web scales horizontally.

### Live ("anlık") standings on `/results/` and `/matches/<id>/`

A live score is written to `ActualResult` as the match goes, so the ganyan
engine keeps GanyanScore / MatchPool current off the running score. `/results/`
and `/matches/<id>/` render that live state **as a result** — a live match shows
its "anlık" puan durumu (who's winning right now), computed off the current
score. Both views ask `live_syncs()` whether each slot is currently live and
pass `is_live` to the templates, which only changes the wording/badge — never
*what* is shown:

- **`/results/`** badges a live card **CANLI** and labels its player list
  "Canlı puan durumu (N)".
- **`/matches/<id>/`** badges the score **CANLI**, adds a "puan durumu anlık …
  maç ilerledikçe değişir" note, titles the standings "Canlı Puan Durumu", and
  softens the empty-pool wording to "şu an kazanan yok" (vs the final "Havuz
  yandı").

Once the match is `FINISHED` (or stuck past its `live_cap`), `is_live` flips
false and both pages drop the live wording — the standings themselves are
unchanged.

## Guaranteed server-side poll (cron)

The visitor trigger only fires while someone is on the homepage, so a match that
finishes with nobody watching — or a dead-of-night kickoff — never gets its final
score polled, and a missed `FINISHED` leaves `MatchSync` stuck `IN_PLAY` past its
cap (shown in neither the live module nor recent results until a human fixes it).
The **`ekiptahmin-live-sync` cron** (`render.yaml`, every 5 min) closes that gap:
`sync_live_results` (a no-op when nothing is in the live window) then
`finalize_stale_syncs` (flips over-but-unfinalized rows). Both are idempotent and
run safely alongside the visitor trigger, which still drives snappy in-play
updates for active viewers. **The cron needs its own `FOOTBALL_DATA_API_KEY`** in
the Render dashboard (`sync:false`), separate from the web service.

## Bracket resolver (`apps/tournament/resolver.py`)

Our code, not the API, decides who advances. `resolve_bracket(tournament)` runs
on every `ActualResult` save (so live sync *and* manual entry advance the tree)
and is idempotent:

1. **Group → R32.** Once a group's matches are all played, its 1st/2nd fill
   their R32 slots; once all 12 groups finish, the 8 best third-placed teams are
   placed via FIFA's allocation table (`data/wc2026/best_third_allocation.json`).
2. **Knockout → next.** Each result pushes its winner (or loser, for the
   third-place match) into the slot it feeds.

Winner determination mirrors scoring: penalties → shootout winner; otherwise the
higher **effective** score (120' if the match went to extra time, else 90').

## Staff result-entry wizard (`/admin/results/`, `apps/scoring/admin_views.py`)

The authoritative path for entering or correcting scores by hand — a wizard
save stamps `source=MANUAL`, which the live sync never overwrites (see *Manual
entry wins* above). The wizard mirrors the prediction wizard's step structure
and saves each row over HTMX.

- **Teams are never picked when known.** A knockout row shows its teams as fixed
  labels with SVG flags, exactly like group rows — the bracket resolver fills
  them from prior results (groups → R32 → R16 → …). A **team picker** appears
  only for a knockout slot that hasn't resolved yet (e.g. its feeding round isn't
  played). Its options are `Name (CODE)` — no flag emoji, which Windows renders
  as a bare two-letter code in a `<select>`.
- **Extra time and penalties are derived, never checkboxes.** A knockout level
  after 90' always went to extra time, so equal 90' scores reveal the 120'
  score inputs (required — the resolver picks the ET winner from them); a 120'
  score still level reveals the penalty winner + shootout score fields. A
  decisive 90' score clears everything beyond regulation.
  `went_to_extra_time`/`went_to_penalties` are computed from the score shape in
  `ActualResultForm.clean`, never toggled. (The raw Django `ActualResult` admin
  still exposes the booleans.)

## Knockout scoring rule (120' vs 90')

For knockout matches, the regulation criteria (exact / diff / result) are judged
on the **120' score when the match went to extra time**, otherwise the 90'
score. Group matches never go to extra time, so they always use 90'. Penalty
criteria are separate (a penalty match is a draw at both 90' and 120'). The
single source of truth is `ActualResult.effective_home_score` /
`effective_away_score`, used by the ganyan bridge and the result-display
templates. See also [scoring-ganyan.md](scoring-ganyan.md).

> The legacy (staff-only) engine is deliberately left on the original 90'-only
> basis as a frozen reference.

## Rollout

1. Buy a football-data plan; put `FOOTBALL_DATA_API_KEY` in **both** the Render
   web service env (the visitor trigger) **and** the `ekiptahmin-live-sync` cron
   env (the guaranteed poll) — each is `sync:false`, so set them in the dashboard.
2. `python manage.py fd_probe --finished` to confirm coverage + mapping.
3. `python manage.py map_external_ids` once (re-run after each knockout round as
   teams resolve).
4. Open the homepage during a live match — the "CANLI" module appears and the
   leaderboard tracks the live score.

## Known follow-ups

- (none currently — manual ET entry landed with the 120' inputs in the wizard,
  and manual results now gate the sync.)
