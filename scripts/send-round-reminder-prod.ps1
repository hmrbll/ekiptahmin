<#
.SYNOPSIS
    Run `manage.py send_round_reminder` against the PRODUCTION database with
    real email delivery (Resend SMTP), from the local machine.

.DESCRIPTION
    Loads .env, then runs the command with production overrides via process
    env (process env wins over .env in django-environ):
      - DJANGO_SETTINGS_MODULE = config.settings.prod  (Resend SMTP backend)
      - DATABASE_URL           = PROD_DATABASE_URL     (reads users/predictions,
                                                        writes EmailLog audit rows)
      - SITE_URL               = https://ekiptahmin.com (links inside the mail)

    Aborts loudly (exit 2) if RESEND_API_KEY is empty in .env and this is not
    a dry run: with prod settings an empty key means the dummy backend
    silently drops every mail, which is exactly the failure mode we refuse to
    run into. Nonzero exit on any failure so a scheduled task can retry
    (e.g. waiting for the round's last dependency result to land, or for the
    key to be added to .env).

    Every run appends to _logs/round_reminder_<date>.log.

.PARAMETER RoundId
    PredictionRound pk to remind for.

.PARAMETER DryRun
    Pass --dry-run through: render everything, send nothing.

.EXAMPLE
    .\scripts\send-round-reminder-prod.ps1 -RoundId 3 -DryRun
    .\scripts\send-round-reminder-prod.ps1 -RoundId 3
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][int]$RoundId,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path $PSScriptRoot -Parent

$LogDir = Join-Path $RepoRoot "_logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir ("round_reminder_{0:yyyyMMdd}.log" -f (Get-Date))

function Log([string]$msg) {
    $line = "{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $msg
    $line | Out-File $LogFile -Append -Encoding utf8
    Write-Host $line
}

function Read-DotEnv([string]$Path) {
    if (-not (Test-Path $Path)) { throw ".env not found at $Path" }
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

Log "=== send-round-reminder-prod start (RoundId=$RoundId, DryRun=$($DryRun.IsPresent)) ==="

$envVars = Read-DotEnv (Join-Path $RepoRoot ".env")

if (-not $envVars["PROD_DATABASE_URL"]) {
    Log "ABORT: PROD_DATABASE_URL is empty in .env."
    exit 2
}
if (-not $envVars["RESEND_API_KEY"] -and -not $DryRun) {
    Log "ABORT: RESEND_API_KEY is empty in .env - prod settings would fall back to the dummy backend and silently drop every mail. Copy the key from the Render dashboard (web service env) into .env, then re-run. A scheduled task will retry automatically."
    exit 2
}

# Production overrides - process env beats .env (django-environ setdefault).
$env:DJANGO_SETTINGS_MODULE = "config.settings.prod"
$env:DATABASE_URL = $envVars["PROD_DATABASE_URL"]
$env:RESEND_API_KEY = $envVars["RESEND_API_KEY"]
$env:SITE_URL = "https://ekiptahmin.com"
$env:ALLOWED_HOSTS = "ekiptahmin.com"
$env:DEBUG = "False"
$env:DEFAULT_FROM_EMAIL = '"ekiptahmin.com" <noreply@ekiptahmin.com>'
# Readable UTF-8 command output in the log (Turkish chars, emoji).
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

$cmdArgs = @("manage.py", "send_round_reminder", "--round-id", $RoundId)
if ($DryRun) { $cmdArgs += "--dry-run" }

# Stderr is merged into the log; under EAP=Stop a mere stderr *line* (e.g. the
# expected dummy-backend WARNING on dry runs) would become a terminating
# NativeCommandError in PowerShell 5.1, so relax it around the native call.
Push-Location $RepoRoot
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $output = & $python @cmdArgs 2>&1
    $exitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEAP
    Pop-Location
}

foreach ($line in $output) { Log ("  " + $line) }
Log "=== exit code $exitCode ==="
exit $exitCode
