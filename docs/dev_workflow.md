# Development Workflow

How code moves from a local change to production, and how production data
gets back into local development. The site is live with real users — `main`
deploys straight to production, so day-to-day work happens on `dev`.

## Branch model

```
dev   — default working branch; all development happens here
        (or on feature branches cut from dev and merged back into it)
main  — production; Render auto-deploys every push; only updated via PR from dev
```

Rules of thumb:

- Never commit directly to `main`. Even a one-line hotfix goes through
  `dev` → PR, so CI runs before it deploys.
- Keep `dev` rebased/merged up to date with `main` after each release so the
  next PR diff stays small.

## CI (GitHub Actions)

[.github/workflows/ci.yml](../.github/workflows/ci.yml) runs **ruff** and
**pytest** (against a Postgres 16 service container) on:

- every push to `dev` and `main`
- every pull request targeting `main`

> **Note:** the repo is private on a GitHub Free plan, so required status
> checks (branch protection) cannot be enforced. GitHub will still show the
> CI result on every PR — the rule is discipline: **don't merge unless CI is
> green.** Making the repo public (or GitHub Pro) would allow enforcing this.

## Releasing dev → main (production deploy)

```powershell
# 1. Work on dev, push — CI runs on the push
git checkout dev
git push origin dev

# 2. Open a PR to main
gh pr create --base main --head dev --title "Release: <short summary>"

# 3. Watch CI on the PR
gh pr checks --watch

# 4. Green? Merge — this push to main is what triggers the Render deploy
gh pr merge --merge

# 5. (optional) Watch the deploy in the Render dashboard; the build runs
#    build.sh: pip + Tailwind + collectstatic + migrate + seeds
```

After the merge, sync local `dev` with `main`:

```powershell
git checkout dev
git pull origin main
git push origin dev
```

## Pulling production data into local dev

[scripts/pull-prod-db.ps1](../scripts/pull-prod-db.ps1) replaces the local
database with a fresh copy of production. There are no media files in this
project, so the database is the whole story.

### One-time setup

1. Render Dashboard → **ekiptahmin-db** → **Connect** → copy the
   **External Database URL**.
2. Put it in `.env` (gitignored — it carries the production password):

   ```
   PROD_DATABASE_URL=postgres://ekiptahmin:...@...frankfurt-postgres.render.com/ekiptahmin?sslmode=require
   ```

   Append `?sslmode=require` if the copied URL doesn't include it.

### Usage

```powershell
.\scripts\pull-prod-db.ps1            # asks for confirmation before dropping local DB
.\scripts\pull-prod-db.ps1 -Force     # no prompt
.\scripts\pull-prod-db.ps1 -KeepDump  # keep the dump file in %TEMP% for re-restores
```

What it does, in order:

1. `pg_dump` production (**read-only** — the prod URL is never passed to a
   client that writes).
2. Drops and recreates the local `ekiptahmin_dev` database
   (refuses to run if the `DATABASE_URL` host is not localhost).
3. `pg_restore` the dump into it.
4. `manage.py migrate` — applies any local migrations that are newer than
   what production has, so the schema matches your working branch.
5. Prints user/prediction row counts as a sanity check.

### Troubleshooting

- **"server version mismatch"** from `pg_dump`: the local client tools are
  older than the Render Postgres server. Install the matching PostgreSQL
  version (client tools are enough) and re-run; the script picks the highest
  version under `C:\Program Files\PostgreSQL` automatically.
- **SSL errors**: make sure the URL ends with `?sslmode=require`.
- **Local password with special characters**: URL-encode them in
  `DATABASE_URL` (same rule as the normal dev setup).
- After a pull, the local DB contains **real user emails**. Dev's email
  backend writes `.eml` files to `_dev_emails/` instead of sending, so
  nothing can reach real inboxes — don't change `EMAIL_BACKEND` in dev.
