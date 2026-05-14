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
│   ├── scoring/                Pure-Python scoring engine
│   ├── leaderboard/            SlotScore cache + 6-level tiebreaker
│   └── public/                 Homepage, live data feeds, public views
├── config/
│   ├── settings/{base,dev,prod}.py
│   ├── middleware.py           AdminLanguageMiddleware
│   └── urls.py
├── data/wc2026/                Seed data (CSV/JSON, idempotent)
├── docs/                       Internal documentation
├── templates/                  Django templates
│   ├── accounts/               Auth pages (signup, login, dashboard, ...)
│   ├── emails/                 Magic-link email templates
│   ├── base.html
│   └── home.html
├── theme/                      django-tailwind theme app
│   └── static_src/             Tailwind source + npm config
├── static/                     Project static assets
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

## Documentation

- [docs/admin.md](docs/admin.md) — Django admin module reference
- Scoring mechanic and project decisions live in private `memory/` (gitignored). High level: bracket-based multi-round predictions with earliest-correct-matchup scoring.
