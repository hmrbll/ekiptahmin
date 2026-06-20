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
  updated every poll. Staff can still overwrite a result. Once a match is
  `FINISHED` the slot is `finalized` and never requested again.
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

# Recompute bracket team assignments from current results (idempotent).
python manage.py resolve_bracket
```

## How scores map (`apps/liveresults/score.py`)

Our canonical score is the **90-minute** result; football-data's `score` object
(v4) is translated as:

| `duration` | 90' score (`home/away_score`) | 120' score (`home/away_score_aet`) | flags |
|---|---|---|---|
| `REGULAR` | `fullTime` (also the live running score) | — | — |
| `EXTRA_TIME` | `regularTime` | `fullTime` | `went_to_extra_time` |
| `PENALTY_SHOOTOUT` | `regularTime` (a draw) | `fullTime` | ET + penalties; `penalties`; winner = more shootout goals |

The penalty winner is derived from the shootout score (more goals advances), not
the provider's `winner` field.

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
by the homepage "CANLI" module (which renders those rows) *and* the "Son
sonuçlar" list (which excludes their slots). Because both honour the cap, a
match stuck `IN_PLAY` (FINISHED missed) drops off the live module after its cap
**and** resurfaces in recent results, instead of vanishing from both. `sync_live_matches` returns immediately (no API call)
when nothing is in the window — this is what keeps a forgotten open tab from
polling football-data overnight. `maybe_sync_live` adds a 45s throttle + a cache
lock so concurrent visitors trigger at most one external call. **Assumes a single
web instance** (the throttle is per-process); revisit if web scales horizontally.

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

1. Buy a football-data plan; put `FOOTBALL_DATA_API_KEY` in the Render web
   service env (the live trigger runs in the web process — no cron needed).
2. `python manage.py fd_probe --finished` to confirm coverage + mapping.
3. `python manage.py map_external_ids` once (re-run after each knockout round as
   teams resolve).
4. Open the homepage during a live match — the "CANLI" module appears and the
   leaderboard tracks the live score.

## Known follow-ups

- **Manual ET entry** doesn't capture the 120' score yet, so a manually-entered
  extra-time knockout result falls back to 90' for scoring/advancement. Not an
  issue for the API path (it captures `*_score_aet`). Add the fields to the
  staff wizard if manual ET entry is needed.
