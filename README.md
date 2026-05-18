# ekiptahmin.com

2026 FIFA Dünya Kupası tahmin oyunu — private league for a friend group.

## Stack

- **Backend:** Django 5 + PostgreSQL
- **Frontend:** Django templates + Tailwind CSS + HTMX
- **Hosting:** Render (Starter plan, Frankfurt)
- **Email:** Resend (SMTP)
- **Auth:** Magic link (passwordless), invite-gated signup

## Project Structure

```
ekiptahmin.com/
├── apps/                       Django apps
│   ├── accounts/               Custom User, Invite, magic-link auth
│   ├── tournament/             Tournament, Stage, Team, PredictionRound,
│   │                           BracketSlot, ActualResult + seed command
│   ├── predictions/            SlotPrediction + full wizard UI
│   ├── scoring/                Two engines in parallel:
│   │                           - ganyan.py  (active: parimutuel pool)
│   │                           - engine.py  (legacy bracket, staff-only)
│   ├── leaderboard/            (placeholder app, scoring app owns aggregation)
│   ├── notifications/          Scheduled/lifecycle emails + staff-only preview
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
│   ├── brand/                  Logo/favicon/OG image set (Sunday Pitch v01)
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
```

### Where do dev emails go?

In dev, a custom file backend writes each email as a `.eml` file to `_dev_emails/`. Double-click the latest one — it opens in the Windows mail viewer (or any mail client) with full HTML rendering, so you can click the magic link directly.

### Email previews

Staff users can render every email template with realistic dummy data at `/ops/emails/preview/`. Each variant (round-open kinds, reminder urgency levels, daily digests) is its own slug — see [apps/notifications/views.py](apps/notifications/views.py) for the registry. Production senders pass the same context shape.

## Deployment

`git push origin main` triggers an auto-deploy on Render via [render.yaml](render.yaml). The build script ([build.sh](build.sh)):

1. Downloads Node 22 LTS (cached between deploys)
2. `pip install -r requirements.txt`
3. `npm ci` + Tailwind production build
4. `collectstatic` + `migrate`

**First production deploy** also requires (run once via Render Shell):

```bash
python manage.py createsuperuser
python manage.py seed_wc2026
```

`RESEND_API_KEY` must be set manually in Render dashboard → Environment. Without it, the prod email backend is `dummy` (sign-up forms succeed but no mail is delivered).

## Theming

Sunday Pitch palette (v01), **light theme only**. Chalk (`#F6F1E4`) page bg, pitch-500 (`#2E6B3F`) primary, clay-500 (`#C2683E`) accent. No dark mode — see project memory for the rationale.

- Tokens live as CSS variables in [theme/static_src/src/styles.css](theme/static_src/src/styles.css) (RGB triplets so Tailwind opacity modifiers work: `bg-primary/10`).
- Tailwind config exposes both fixed scales (`pitch`, `clay`, `stone`, `success`, `warning`, `danger`) and semantic aliases (`page`, `surface`, `fg`, `fg-muted`, `line`, `primary`, `accent`, ...). Templates should prefer semantic names.
- Fonts: Bricolage Grotesque (display), Geist (body), JetBrains Mono (code) — all via Google Fonts.

## Documentation

- [docs/scoring-ganyan.md](docs/scoring-ganyan.md) — Active scoring mechanic: parimutuel pool model with per-stage pool sizes and round-weight effective-round picking.
- [docs/admin.md](docs/admin.md) — Django admin module reference
- [docs/email_setup.md](docs/email_setup.md) — Email infrastructure (Resend SMTP + DNS)
- Project decisions and session-to-session context live in private `memory/` (gitignored).

### Scoring at a glance

Each match has fixed pools per criterion (exact / diff / result / penalty_pass). Pools split equally among users who get that criterion right — single-prediction wins pay the full pool, consensus picks pay a thin slice. For each `(user, match)` the engine picks ONE **effective round** (the round whose prediction + weight maximises the user's payout); criteria are paid from that round only. Pools that no one hits **burn**.

Legacy bracket scoring (`apps/scoring/engine.py`, `SlotScore`) still runs in parallel for staff comparison at `/legacy/leaderboard/`, `/legacy/results/`, `/legacy/scoring-diff/` — see [docs/scoring-ganyan.md](docs/scoring-ganyan.md) for the full spec and rationale.

### Common commands (scoring-specific)

```powershell
python manage.py recompute_scores     # rebuild legacy SlotScore cache
python manage.py recompute_ganyan     # rebuild GanyanScore + MatchPool cache
```

Both are idempotent; trigger after Stage pool edits in admin (signals don't fire on Stage saves).
