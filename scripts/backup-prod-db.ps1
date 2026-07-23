<#
.SYNOPSIS
    Take a durable, read-only backup of the production database before shutting
    the service down. Does NOT touch the local database.

.DESCRIPTION
    Dumps the production database (PROD_DATABASE_URL from .env) to timestamped
    files under .\backups\ (gitignored). Two formats are written for safety:

      * .dump  - pg_dump custom format, restore later with pg_restore.
      * .sql   - plain SQL, human-readable and restorable with psql, and
                 readable even without the exact PostgreSQL server version.

    The production URL is only ever passed to pg_dump, a read-only client.
    The local database is never opened, dropped, or modified.

    Run this before deleting the Render database / cancelling paid services.
    Keep the resulting files somewhere safe (they contain all app data).

.EXAMPLE
    .\scripts\backup-prod-db.ps1
#>
[CmdletBinding()]
param()

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
    throw "PROD_DATABASE_URL is missing/empty in .env. Get it from Render Dashboard -> ekiptahmin-db -> Connect -> External Database URL."
}

# --- Locate PostgreSQL client tools ------------------------------------------
# Use the newest pg_dump we can find: it must be at least as new as the Render
# server (PG 18 as of 2026-06), which may be newer than the local server.
$candidates = @()
foreach ($dir in (Get-ChildItem "C:\Program Files\PostgreSQL" -Directory -ErrorAction SilentlyContinue)) {
    if ($dir.Name -match "^(\d+)$" -and (Test-Path (Join-Path $dir.FullName "bin\pg_dump.exe"))) {
        $candidates += @{ Bin = (Join-Path $dir.FullName "bin"); Version = [int]$Matches[1] }
    }
}
foreach ($dir in (Get-ChildItem (Join-Path $env:LOCALAPPDATA "Programs") -Directory -Filter "pgsql-*" -ErrorAction SilentlyContinue)) {
    if ($dir.Name -match "^pgsql-(\d+)$" -and (Test-Path (Join-Path $dir.FullName "bin\pg_dump.exe"))) {
        $candidates += @{ Bin = (Join-Path $dir.FullName "bin"); Version = [int]$Matches[1] }
    }
}
$onPath = Get-Command pg_dump -ErrorAction SilentlyContinue
if ($onPath -and ((& $onPath.Source --version) -match "(\d+)(?:\.\d+)?\s*$")) {
    $candidates += @{ Bin = (Split-Path $onPath.Source -Parent); Version = [int]$Matches[1] }
}
if (-not $candidates) {
    throw "pg_dump not found (PATH, C:\Program Files\PostgreSQL, $env:LOCALAPPDATA\Programs\pgsql-*). Install PostgreSQL client tools first."
}
$best = $candidates | Sort-Object { $_.Version } -Descending | Select-Object -First 1
$pgDump = Join-Path $best.Bin "pg_dump.exe"
Write-Host "Using pg_dump: $pgDump (PG $($best.Version))"

# --- Destination -------------------------------------------------------------
$backupDir = Join-Path $RepoRoot "backups"
if (-not (Test-Path $backupDir)) { New-Item -ItemType Directory -Path $backupDir | Out-Null }
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$dumpPath = Join-Path $backupDir "ekiptahmin_prod_$timestamp.dump"
$sqlPath  = Join-Path $backupDir "ekiptahmin_prod_$timestamp.sql"

# --- 1. Custom-format dump (for pg_restore) ----------------------------------
Write-Host ""
Write-Host "[1/2] Dumping production (custom format, read-only)..."
& $pgDump --format=custom --no-owner --no-privileges --file=$dumpPath $prodUrl
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "pg_dump failed. If it mentions a 'server version mismatch', your" -ForegroundColor Red
    Write-Host "local client tools are older than the Render server - install the" -ForegroundColor Red
    Write-Host "matching PostgreSQL version and re-run." -ForegroundColor Red
    exit 1
}
$dumpSize = "{0:N1} MB" -f ((Get-Item $dumpPath).Length / 1MB)
Write-Host "      -> $dumpPath ($dumpSize)"

# --- 2. Plain-SQL dump (human-readable, version-independent) ------------------
Write-Host "[2/2] Dumping production (plain SQL, read-only)..."
& $pgDump --format=plain --no-owner --no-privileges --file=$sqlPath $prodUrl
if ($LASTEXITCODE -ne 0) {
    Write-Host "Plain-SQL dump failed (custom-format .dump above is still valid)." -ForegroundColor Yellow
} else {
    $sqlSize = "{0:N1} MB" -f ((Get-Item $sqlPath).Length / 1MB)
    Write-Host "      -> $sqlPath ($sqlSize)"
}

Write-Host ""
Write-Host "Backup complete. Files are under: $backupDir" -ForegroundColor Green
Write-Host "Keep these safe - they contain ALL production data. The folder is gitignored." -ForegroundColor Green
Write-Host ""
Write-Host "To restore later into a fresh local DB:"
Write-Host "  createdb ekiptahmin"
Write-Host "  pg_restore --no-owner --no-privileges -d ekiptahmin `"$dumpPath`""
