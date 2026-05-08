# ekiptahmin.com

2026 FIFA Dünya Kupası tahmin oyunu — private league for a friend group.

## Stack
- **Backend:** Django 5 + PostgreSQL
- **Frontend:** Django templates + Tailwind CSS + HTMX
- **Hosting:** Render (Frankfurt region)
- **Email:** Resend
- **Auth:** Magic link (passwordless)

## Project Structure

```
ekiptahmin.com/
├── apps/
│   ├── accounts/       # Custom User, magic link auth
│   ├── tournament/     # Tournament, Stage, Team, BracketSlot
│   ├── predictions/    # PredictionRound, SlotPrediction
│   ├── scoring/        # Scoring engine (pure-Python service)
│   └── leaderboard/    # Leaderboard view + caching
├── config/
│   └── settings/{base,dev,prod}.py
├── templates/
├── static/
└── tests/
```

## Local Development

### Prerequisites
- Python 3.12+
- PostgreSQL 17+
- Node.js 22+ (for Tailwind builds)

### Setup
```powershell
# 1. Clone the repo & enter
git clone <repo-url> ekiptahmin.com
cd ekiptahmin.com

# 2. Virtualenv
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements-dev.txt

# 4. Configure environment
Copy-Item .env.example .env
# Edit .env: set SECRET_KEY (quote if it contains #) and DATABASE_URL with your Postgres password (URL-encode special chars)

# 5. Create local database
$env:PGPASSWORD = '<your_postgres_password>'
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h localhost -d postgres -c "CREATE DATABASE ekiptahmin_dev;"

# 6. Run migrations & create superuser
python manage.py migrate
python manage.py createsuperuser

# 7. Start dev server
python manage.py runserver
# → http://127.0.0.1:8000/admin/
```

### Common commands
```powershell
python manage.py makemigrations
python manage.py migrate
python manage.py shell
pytest
ruff check .
ruff format .
```

## Deployment

Pushes to `main` auto-deploy to Render via `render.yaml` Blueprint.

- **Web service:** Starter plan ($7/mo)
- **PostgreSQL:** Starter plan ($7/mo, daily backups)
- **Region:** Frankfurt (closest to TR)

`RESEND_API_KEY` must be set manually in Render dashboard (not auto-synced for security).

## Scoring Mechanic

Bracket-based, multi-round predictions. Each user predicts the entire tournament bracket. As actual results come in, the scoring engine evaluates each user's earliest correct matchup prediction against the result, multiplied by that round's weight. See [project memory](memory/project_scoring_mechanic.md) for full details.
