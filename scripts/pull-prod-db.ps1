<#
.SYNOPSIS
    Pull the production database from Render into the local PostgreSQL instance.

.DESCRIPTION
    Dumps the production database (read-only: pg_dump) using PROD_DATABASE_URL
    from .env, then DROPS and recreates the local database from DATABASE_URL
    and restores the dump into it. Finishes with `manage.py migrate` so local
    migrations that are newer than production get applied.

    Safety:
      - The production URL is only ever passed to pg_dump (a read-only client).
      - The restore target must be localhost/127.0.0.1 or the script aborts.
      - The local database is only dropped after an explicit confirmation
        (skip with -Force).

.PARAMETER Force
    Skip the confirmation prompt before dropping the local database.

.PARAMETER KeepDump
    Keep the dump file in %TEMP% instead of deleting it after a successful
    restore (useful for restoring again later without hitting production).

.EXAMPLE
    .\scripts\pull-prod-db.ps1
    .\scripts\pull-prod-db.ps1 -Force -KeepDump
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$KeepDump
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path $PSScriptRoot -Parent

function Read-DotEnv([string]$Path) {
    if (-not (Test-Path $Path)) {
        throw ".env not found at $Path - copy .env.example and fill it in first."
    }
    $vars = @{}
    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
        $idx = $trimmed.IndexOf("=")
        if ($idx -lt 1) { continue }
        $key = $trimmed.Substring(0, $idx).Trim()
        $value = $trimmed.Substring($idx + 1).Trim().Trim('"').Trim("'")
        $vars[$key] = $value
    }
    return $vars
}

# --- Load configuration -----------------------------------------------------
$envVars = Read-DotEnv (Join-Path $RepoRoot ".env")

$prodUrl = $envVars["PROD_DATABASE_URL"]
if (-not $prodUrl) {
    throw "PROD_DATABASE_URL is missing/empty in .env. Get it from Render Dashboard -> ekiptahmin-db -> Connect -> External Database URL (see docs/dev_workflow.md)."
}

$localUrl = $envVars["DATABASE_URL"]
if (-not $localUrl) {
    throw "DATABASE_URL is missing/empty in .env."
}

# Parse the LOCAL url into components (needed for drop/create via psql).
$urlPattern = "^postgres(?:ql)?://(?<user>[^:@/]+):(?<pass>[^@]+)@(?<host>[^:/]+)(?::(?<port>\d+))?/(?<db>[^/?]+)"
$m = [regex]::Match($localUrl, $urlPattern)
if (-not $m.Success) {
    throw "Could not parse DATABASE_URL (expected postgres://USER:PASSWORD@HOST:PORT/DBNAME)."
}
$local = @{
    User = $m.Groups["user"].Value
    Pass = $m.Groups["pass"].Value
    Host = $m.Groups["host"].Value
    Port = if ($m.Groups["port"].Success) { $m.Groups["port"].Value } else { "5432" }
    Db   = $m.Groups["db"].Value
}

# --- Safety: never restore anywhere but localhost ---------------------------
if ($local.Host -notin @("localhost", "127.0.0.1")) {
    throw "Refusing to run: DATABASE_URL host is '$($local.Host)', not localhost. This script only restores into a local database."
}

# --- Locate PostgreSQL client tools ------------------------------------------
$pgBin = $null
$onPath = Get-Command pg_dump -ErrorAction SilentlyContinue
if ($onPath) {
    $pgBin = Split-Path $onPath.Source -Parent
} else {
    $installs = Get-ChildItem "C:\Program Files\PostgreSQL" -Directory -ErrorAction SilentlyContinue |
        Sort-Object { [int]$_.Name } -Descending
    foreach ($dir in $installs) {
        if (Test-Path (Join-Path $dir.FullName "bin\pg_dump.exe")) {
            $pgBin = Join-Path $dir.FullName "bin"
            break
        }
    }
}
if (-not $pgBin) {
    throw "pg_dump not found on PATH or under C:\Program Files\PostgreSQL. Install PostgreSQL client tools."
}
$pgDump = Join-Path $pgBin "pg_dump.exe"
$pgRestore = Join-Path $pgBin "pg_restore.exe"
$psql = Join-Path $pgBin "psql.exe"
Write-Host "Using PostgreSQL client tools: $pgBin"

# --- Confirm the destructive part --------------------------------------------
Write-Host ""
Write-Host "This will DROP local database '$($local.Db)' on $($local.Host):$($local.Port) and replace it with a fresh copy of production." -ForegroundColor Yellow
if (-not $Force) {
    $answer = Read-Host "Type 'yes' to continue"
    if ($answer -ne "yes") {
        Write-Host "Aborted."
        exit 1
    }
}

# --- 1. Dump production (read-only) ------------------------------------------
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$dumpPath = Join-Path $env:TEMP "ekiptahmin_prod_$timestamp.dump"
Write-Host ""
Write-Host "[1/4] Dumping production database (pg_dump, read-only)..."
& $pgDump --format=custom --no-owner --no-privileges --file=$dumpPath $prodUrl
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "pg_dump failed. If the error above mentions a 'server version mismatch'," -ForegroundColor Red
    Write-Host "your local client tools are older than the Render Postgres server -" -ForegroundColor Red
    Write-Host "install the matching PostgreSQL version (see docs/dev_workflow.md)." -ForegroundColor Red
    exit 1
}
$dumpSize = "{0:N1} MB" -f ((Get-Item $dumpPath).Length / 1MB)
Write-Host "      Dump written to $dumpPath ($dumpSize)"

# --- 2. Drop & recreate the local database -----------------------------------
Write-Host "[2/4] Recreating local database '$($local.Db)'..."
$env:PGPASSWORD = $local.Pass
try {
    & $psql -h $local.Host -p $local.Port -U $local.User -d postgres -v ON_ERROR_STOP=1 `
        -c "DROP DATABASE IF EXISTS `"$($local.Db)`" WITH (FORCE);" `
        -c "CREATE DATABASE `"$($local.Db)`";" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to recreate local database." }

    # --- 3. Restore the dump --------------------------------------------------
    Write-Host "[3/4] Restoring dump into '$($local.Db)'..."
    & $pgRestore --no-owner --no-privileges --single-transaction `
        -h $local.Host -p $local.Port -U $local.User -d $local.Db $dumpPath
    if ($LASTEXITCODE -ne 0) { throw "pg_restore failed - local database may be incomplete. Re-run the script." }

    # --- 4. Apply local migrations + sanity output ----------------------------
    Write-Host "[4/4] Applying local migrations (manage.py migrate)..."
    $python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) { $python = "python" }
    Push-Location $RepoRoot
    try {
        & $python manage.py migrate --no-input
        if ($LASTEXITCODE -ne 0) { throw "manage.py migrate failed." }
    } finally {
        Pop-Location
    }

    Write-Host ""
    Write-Host "Sanity check (row counts):"
    & $psql -h $local.Host -p $local.Port -U $local.User -d $local.Db -t -A `
        -c "SELECT 'users: ' || count(*) FROM accounts_user;" `
        -c "SELECT 'predictions: ' || count(*) FROM predictions_slotprediction;"
} finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}

if ($KeepDump) {
    Write-Host ""
    Write-Host "Dump kept at: $dumpPath"
} else {
    Remove-Item $dumpPath -Force
}

Write-Host ""
Write-Host "Done. Local '$($local.Db)' now mirrors production (plus any local migrations)." -ForegroundColor Green
