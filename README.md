# ekiptahmin.com

2026 FIFA Dünya Kupası tahmin oyunu — private league for a friend group.

## Stack

- **Backend:** Django 5 + PostgreSQL
- **Frontend:** Django templates + Tailwind CSS + HTMX
- **Hosting:** Render (Starter plan, Frankfurt)
- **Email:** Resend (SMTP)
- **Auth:** Magic link (passwordless), invite-gated signup
- **Monitoring:** Sentry (errors only, prod; no-op unless `SENTRY_DSN` is set)

## Project Structure

```
ekiptahmin.com/
├── apps/                       Django apps
│   ├── accounts/               Custom User, Invite, magic-link auth
│   ├── tournament/             Tournament, Stage, Team, PredictionRound,
│   │                           BracketSlot, ActualResult + seed command
│   ├── predictions/            SlotPrediction, bracket cascade (derivation +
│   │                           stale-prediction invalidation) + full wizard UI
│   ├── scoring/                Two engines in parallel:
│   │                           - ganyan.py  (active: parimutuel pool)
│   │                           - engine.py  (legacy bracket, staff-only)
│   ├── leaderboard/            (placeholder app, scoring app owns aggregation)
│   ├── notifications/          Scheduled/lifecycle emails + staff-only preview
│   ├── liveresults/            football-data.org live sync (client, score map,
│   │                           MatchSync, throttled trigger, homepage module)
│   └── public/                 Homepage, live data feeds, public views
├── config/
│   ├── settings/{base,dev,prod}.py
│   ├── middleware.py           AdminLanguageMiddleware
│   └── urls.py
├── data/wc2026/                Seed data (CSV/JSON, idempotent)
├── docs/                       Internal documentation
├── templates/                  Django templates
│   ├── accounts/               Auth pages (signup, login, dashboard, ...)
│   ├── emails/                 Email templates (magic link, invite, round
│   │                           reminders, daily digest) + shared _footer
│   ├── base.html
│   ├── home.html
│   └── rules.html             Public rules + scoring reference
├── theme/                      django-tailwind theme app
│   └── static_src/             Tailwind source + npm config (brand palette)
├── static/                     Project static assets
│   ├── brand/                  Logo/favicon/OG image set (Sunday Pitch v02 lockup)
│   └── flags/                  Country flag SVGs
├── build.sh                    Render build script
├── render.yaml                 Render Blueprint
└── manage.py                   (sets UTF-8 stdout for Windows)
```

Tests live next to the apps they cover, e.g., [apps/accounts/tests/](apps/accounts/tests/).

## Local Development

### Prerequisites

- Python 3.12+
- PostgreSQL 17+
- Node.js 22+ (for Tailwind builds)

### Setup (Windows / PowerShell)

```powershell
# Clone & enter
git clone https://github.com/hmrbll/ekiptahmin.git
cd ekiptahmin

# Python virtualenv
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

# Tailwind / npm dependencies
python manage.py tailwind install

# Configure environment
Copy-Item .env.example .env
# Edit .env: set SECRET_KEY (quote with double-quotes if it contains '#')
# and DATABASE_URL (URL-encode special chars in your Postgres password)

# Create local database
$env:PGPASSWORD = '<your_postgres_password>'
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h localhost -d postgres -c "CREATE DATABASE ekiptahmin_dev;"

# Migrate, seed, create local superuser
python manage.py migrate
python manage.py seed_wc2026
python manage.py createsuperuser

# Start dev server (in one terminal)
python manage.py runserver

# Tailwind watcher (in a second terminal — auto-rebuilds CSS on template changes)
python manage.py tailwind start
```

Open `http://127.0.0.1:8000/`. Admin lives at `/admin/`.

### Common commands

```powershell
python manage.py makemigrations          # generate migrations
python manage.py migrate                 # apply migrations
python manage.py shell                   # Django shell
python manage.py seed_wc2026             # (re)load WC 2026 fixture data
python manage.py tailwind build          # one-shot Tailwind production build
pytest                                   # run all tests
ruff check . && ruff format .            # lint + format

# Live results (needs FOOTBALL_DATA_API_KEY in .env) — see docs/live-results.md
python manage.py fd_probe --finished     # read-only API probe (no DB writes)
python manage.py map_external_ids        # map match ids onto bracket slots
python manage.py sync_live_results --dry-run   # one sync pass, preview only
```

### Where do dev emails go?

In dev, a custom file backend writes each email as a `.eml` file to `_dev_emails/`. Double-click the latest one — it opens in the Windows mail viewer (or any mail client) with full HTML rendering, so you can click the magic link directly.

### Email previews & audit

Staff users can render every email template with realistic dummy data at `/ops/emails/preview/`. Each variant (invite, magic-link signup/login, daily digests) is its own slug — see [apps/notifications/views.py](apps/notifications/views.py) for the registry. Production senders pass the same context shape.

`/ops/emails/` (staff-only, linked as "Mailler" in the header) is the audit log of every mail actually sent — filterable by status/kind, paginated. Every lifecycle sender routes through `send_logged`, which records an `EmailLog` row and never raises, so a failed send surfaces here as a `FAILED` row instead of erroring the form.

## Development Workflow

The site is live — `main` deploys straight to production, so all work happens on the `dev` branch:

1. Commit & push to `dev` → CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs ruff + pytest.
2. `gh pr create --base main --head dev` → CI result shows on the PR.
3. Merge when green → Render auto-deploys `main`.

To develop against real data, pull a copy of the production database into local Postgres:

```powershell
.\scripts\pull-prod-db.ps1    # needs PROD_DATABASE_URL in .env (see docs)
```

Full runbook (branch rules, release steps, prod-data sync details & troubleshooting): [docs/dev_workflow.md](docs/dev_workflow.md).

## Deployment

Merging a PR into `main` (see [Development Workflow](#development-workflow)) triggers an auto-deploy on Render via [render.yaml](render.yaml). The build script ([build.sh](build.sh)):

1. Downloads Node 22 LTS (cached between deploys)
2. `pip install -r requirements.txt`
3. `npm ci` + Tailwind production build
4. `collectstatic` + `migrate`
5. `seed_wc2026` (idempotent — re-syncs tournament fixtures from `data/wc2026/`; **prediction rounds are the exception**: they're created once and admin-owned after that, so mid-tournament admin edits — closed stages, moved deadlines — survive deploys)
6. `recompute_ganyan` (idempotent — backfills `GanyanScore` + `MatchPool` for any slot whose post_save signal got missed)

**First production deploy** only needs a superuser (everything else is in the build):

```bash
python manage.py createsuperuser
```

Some env vars are `sync: false` in [render.yaml](render.yaml) — Render does **not** populate them, you set each manually in the dashboard (per service where it applies):

- `RESEND_API_KEY` — web + **both** digest crons (not inherited from web). Without it the prod email backend is `dummy`: forms succeed but no mail is delivered. See [docs/email_setup.md](docs/email_setup.md).
- `FOOTBALL_DATA_API_KEY` — web (visitor trigger) + the `ekiptahmin-live-sync` cron (the guaranteed poll). Unset on either → that path silently no-ops. See [docs/live-results.md](docs/live-results.md).
- `RESEND_WEBHOOK_SECRET` — web only. Svix secret for the bounce/complaint webhook; unset → the endpoint rejects everything (503).
- `SENTRY_DSN` — web + all three crons. Unset → Sentry is a no-op (no error reporting).

### Launch / ops commands

Run from **Render Shell** (these are one-shot operations, not part of the build):

```bash
python manage.py send_test_email you@example.com    # verify the email backend actually delivers

# Wipe all test data before going live. Deletes non-staff users (CASCADE removes
# their predictions/scores), clears staff users' predictions, wipes score caches,
# deletes all match results (ActualResult) and reverts resolved knockout teams to
# NULL. Keeps staff accounts and invites. Idempotent; dry-run by default.
python manage.py reset_for_launch                    # preview
python manage.py reset_for_launch --confirm          # execute

# Bulk-create invites + email the welcome link to each address (self-signup:
# the recipient picks their own nickname). Skips already-registered emails
# and addresses with an active invite.
python manage.py send_invites --emails "a@x.com,b@y.com" --dry-run
python manage.py send_invites --file invites.txt     # one address per line ("email" or "email,note")

# Delete stale knockout predictions (matchup no longer derivable from the
# user's upstream predictions) in all OPEN rounds. Backfill for rows written
# before save-time invalidation shipped (or let through by a since-fixed
# bug); closed rounds are scored history and are never touched.
python manage.py revalidate_predictions --dry-run    # preview, rolls back
python manage.py revalidate_predictions              # execute

# Who is missing which predictions in the open rounds (editable stages only)?
# Complements revalidate_predictions: that finds stale rows, this finds gaps.
# Run before a deadline to know who to nudge.
python manage.py missing_predictions

# Pre-create accounts with nicknames YOU choose + email each a one-click
# onboarding link. The link logs them straight in (no signup form, no 15-min
# magic-link expiry) via the invite auto-login branch in accounts.views; it's
# long-lived and reusable. Idempotent.
python manage.py onboard_players --players "Ali:oyuncu1@x.com,Can:k@x.com" --dry-run
python manage.py onboard_players --players "Ali:oyuncu1@x.com,Can:k@x.com"
```

Do **not** add `reset_for_launch` to `build.sh` — it would wipe data on every deploy.

> `send_invites` = self-signup (recipient chooses nickname). `onboard_players` =
> you set the nickname and the link logs them in directly. A pre-created account's
> invite link auto-logs-in (see `apps/accounts/views.py::invite_signup`); normal
> invites with no account yet still show the signup form.

## Theming

Sunday Pitch palette, **light theme only**. Chalk (`#F6F1E4`) page bg, pitch-500 (`#2E6B3F`) primary, clay-500 (`#C2683E`) accent. No dark mode — dark mode was attempted and dropped (flag/contrast issues + UA-level forced dark mode); `:root { color-scheme: only light; }` opts out at the browser level.

- Tokens live as CSS variables in [theme/static_src/src/styles.css](theme/static_src/src/styles.css) (RGB triplets so Tailwind opacity modifiers work: `bg-primary/10`).
- Tailwind config exposes both fixed scales (`pitch`, `clay`, `stone`, `success`, `warning`, `danger`) and semantic aliases (`page`, `surface`, `fg`, `fg-muted`, `line`, `primary`, `accent`, ...). Templates should prefer semantic names.
- Fonts: Bricolage Grotesque (display), Geist (body), JetBrains Mono (code) — all via Google Fonts.

## Documentation

- [docs/scoring-ganyan.md](docs/scoring-ganyan.md) — Active scoring mechanic: parimutuel pool model with per-stage pool sizes and round-weight effective-round picking.
- [docs/live-results.md](docs/live-results.md) — football-data.org live sync: score mapping, match identity, throttled trigger, bracket resolver, and the 120'-vs-90' knockout scoring rule.
- [docs/admin.md](docs/admin.md) — Django admin module reference
- [docs/email_setup.md](docs/email_setup.md) — Email infrastructure (Resend SMTP + DNS)
- [docs/dev_workflow.md](docs/dev_workflow.md) — Branch model, CI, release flow, prod-data sync
- Project decisions and session-to-session context live in private `memory/` (gitignored).

### Scoring at a glance

Each match has fixed pools per criterion: regulation (exact / diff / result, 100 each) plus — on knockout matches that go to penalties — three penalty pools (penalty_winner / penalty_score / penalty_diff, 25 each). Pools split equally among users who get that criterion right — single-prediction wins pay the full pool, consensus picks pay a thin slice. For each `(user, match)` the engine picks ONE **effective round** (the round whose prediction + weight scores highest against the *full* pools — a choice that depends only on that user's own picks, so the slot resolves in one deterministic pass); criteria are then paid from that round only, using the diluted payouts. Pools that no one hits **burn**. Pool sizes are admin-tunable and persist across deploys (seed sets them only on first creation).

For knockout matches the regulation criteria are judged on the **120' score when the match went to extra time**, otherwise the 90' score (group matches never go to ET, so always 90'). See [docs/live-results.md](docs/live-results.md#knockout-scoring-rule-120-vs-90).

Legacy bracket scoring (`apps/scoring/engine.py`, `SlotScore`) still runs in parallel for staff comparison at `/legacy/leaderboard/`, `/legacy/results/`, `/legacy/scoring-diff/` — see [docs/scoring-ganyan.md](docs/scoring-ganyan.md) for the full spec and rationale.

### Bracket cascade at a glance

Knockout slot teams are derived per user from their own predictions **in that same round** — upstream slot winner/loser, group standings, or FIFA's best-third allocation table ([apps/predictions/cascade.py](apps/predictions/cascade.py)). Rounds are **isolated**: a later round never inherits an earlier round's bracket, so e.g. Son 16 in "Grup sonrası" derives from that round's own Son 32 picks — if they're missing, the slot is blocked ("predict Son 32 first") rather than showing the pre-tournament matchup. The one shared cross-round foundation is **actual results**: as games are played, `tournament.resolver` writes the real teams into `BracketSlot.*_team_actual`, which overrides all prediction-derived teams in every round. Editing an upstream prediction re-derives every downstream matchup in the same round: any stored prediction whose matchup went stale is **deleted automatically** (the slot shows as never predicted), recursively down the bracket. Closed rounds are scored history and are never touched. Affected rows on multi-stage pages refresh in place via HTMX out-of-band swaps. Earlier rounds' picks for the same resolved matchup still show read-only as references (never prefilled).

When an edit turns a draw prediction into a decisive score, the browser still submits the (CSS-hidden) penalty-shootout inputs; the form clears those stale fields server-side instead of rejecting the save — otherwise the validation error would render inside the hidden section and the save would fail silently, leaving downstream matchups stale.

The shootout winner (`SlotPrediction.penalty_winner`) is never entered by the user: a shootout cannot end level, so `SlotPrediction.clean()` derives it from the shootout score on every validated save (and clears it when the prediction is decisive). The prediction form only asks for the penalty score.

### Mid-tournament stage locking

Closing predictions for a stage mid-round = removing it from the round's `editable_stages` in admin (rounds are admin-owned — deploys don't revert this). The wizard keeps a closed stage visible to users who predicted it in that round: its steps render read-only (static rows instead of forms, 🔒 markers on the step pills) and the live group standings stay. The same read-only rendering kicks in when the round's deadline has passed or a slot's kickoff is in the past, and the round entry redirect skips locked steps to land on the first still-editable one.

### Common commands (scoring-specific)

```powershell
python manage.py recompute_scores     # rebuild legacy SlotScore cache
python manage.py recompute_ganyan     # rebuild GanyanScore + MatchPool cache
```

Both are idempotent; trigger after Stage pool edits in admin (signals don't fire on Stage saves).
