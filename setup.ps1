# setup.ps1 — Artha Turbo Setup for Windows
# Mirrors the behaviour of setup.sh on macOS/Linux.
#
# Usage (from a PowerShell prompt in the Artha directory):
#   Set-ExecutionPolicy RemoteSigned -Scope CurrentUser   # run once if needed
#   .\setup.ps1
#
# What this script does:
#   [1/5] Check prerequisites: Python 3.11+, Git, age
#   [2/5] Create / activate virtual environment
#   [3/5] Install dependencies
#   [4/5] Copy starter profile (if none exists)
#   [5/5] Run demo briefing + offer setup wizard
#
# Zero new binary dependencies — uses only Python, Git, and age (all documented
# as Artha prerequisites).  See docs/quickstart.md for manual steps.
#
# Ref: specs/improve.md §8 I-11

$ErrorActionPreference = "Stop"

# ── Colour helpers ────────────────────────────────────────────────────────────
function Write-Pass  { param($msg) Write-Host "  [OK]  $msg" -ForegroundColor Green }
function Write-Fail  { param($msg) Write-Host " [FAIL] $msg" -ForegroundColor Red }
function Write-Warn  { param($msg) Write-Host " [WARN] $msg" -ForegroundColor Yellow }
function Write-Info  { param($msg) Write-Host "  [->]  $msg" -ForegroundColor Cyan }
function Write-Step  { param($msg) Write-Host "`n$msg" -ForegroundColor White }

# ── Locate the script's own directory (handles being called from any cwd) ─────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║  Artha — Personal Intelligence OS   Windows Quick Setup ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── [1/5] Prerequisites ───────────────────────────────────────────────────────
Write-Step "[1/5] Checking prerequisites..."

# Python: try 'python', then the Windows py launcher
$PythonCmd = $null
$PythonVersion = $null

foreach ($candidate in @("python", "py")) {
    try {
        $verOutput = & $candidate --version 2>&1 | Select-Object -First 1
        if ($verOutput -match "Python (\d+)\.(\d+)") {
            $maj = [int]$Matches[1]
            $min = [int]$Matches[2]
            if ($maj -gt 3 -or ($maj -eq 3 -and $min -ge 11)) {
                $PythonCmd = $candidate
                $PythonVersion = "Python $maj.$min"
                break
            }
        }
    } catch { }
}

if ($PythonCmd) {
    Write-Pass "$PythonVersion (via '$PythonCmd')"
} else {
    Write-Fail "Python 3.11+ not found."
    Write-Warn "Install from https://www.python.org/downloads/ or the Microsoft Store."
    Write-Warn "After installing, re-run this script."
    exit 1
}

# Git
try {
    $gitVer = git --version 2>&1 | Select-Object -First 1
    Write-Pass $gitVer
} catch {
    Write-Warn "Git not found — recommended but not required for basic use."
    Write-Warn "Install from https://git-scm.com/download/win"
}

# age encryption binary
$ageFound = $false
try {
    $ageVer = age --version 2>&1 | Select-Object -First 1
    Write-Pass "age $ageVer"
    $ageFound = $true
} catch {
    Write-Warn "age not found — encryption will be unavailable until installed."
    Write-Warn "Install via: winget install FiloSottile.age"
    Write-Warn "Or download from: https://github.com/FiloSottile/age/releases"
}

# ── [2/5] Virtual environment ─────────────────────────────────────────────────
Write-Step "[2/5] Setting up virtual environment..."

$VenvRoot = Join-Path $HOME ".artha-venvs"
$VenvPath = Join-Path $VenvRoot ".venv-win"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
$VenvActivate = Join-Path $VenvPath "Scripts\Activate.ps1"

if (-not (Test-Path $VenvPath)) {
    Write-Info "Creating venv at $VenvPath ..."
    & $PythonCmd -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Failed to create virtual environment."
        exit 1
    }
    Write-Pass "Virtual environment created."
} else {
    Write-Pass "Virtual environment already exists at $VenvPath"
}

# Activate the venv for the rest of this script
& $VenvActivate

# ── [3/5] Install dependencies ────────────────────────────────────────────────
Write-Step "[3/5] Installing dependencies..."

$RequirementsFile = Join-Path $ScriptDir "scripts\requirements.txt"
if (-not (Test-Path $RequirementsFile)) {
    Write-Fail "requirements.txt not found at $RequirementsFile"
    exit 1
}

Write-Info "Running: pip install -r scripts\requirements.txt"
& $VenvPython -m pip install -r $RequirementsFile --quiet --upgrade
if ($LASTEXITCODE -ne 0) {
    Write-Fail "pip install failed — check your internet connection."
    exit 1
}
Write-Pass "Dependencies installed."

# ── [4/5] Profile template ────────────────────────────────────────────────────
Write-Step "[4/5] Checking profile..."

$ProfileFile  = Join-Path $ScriptDir "config\user_profile.yaml"
$StarterFile  = Join-Path $ScriptDir "config\user_profile.starter.yaml"
$ExampleFile  = Join-Path $ScriptDir "config\user_profile.example.yaml"

if (-not (Test-Path $ProfileFile)) {
    if (Test-Path $StarterFile) {
        Copy-Item $StarterFile $ProfileFile
        Write-Pass "Starter profile copied → config\user_profile.yaml"
    } elseif (Test-Path $ExampleFile) {
        Copy-Item $ExampleFile $ProfileFile
        Write-Pass "Example profile copied → config\user_profile.yaml"
    } else {
        Write-Warn "No profile template found — you'll need to create config\user_profile.yaml manually."
    }
} else {
    Write-Pass "Profile already exists at config\user_profile.yaml"
}

# Install PII git hook (best-effort — no failure if .git absent)
$HookDest = Join-Path $ScriptDir ".git\hooks\pre-commit"
$HookScript = Join-Path $ScriptDir "scripts\vault_hook.py"
if (Test-Path (Join-Path $ScriptDir ".git")) {
    if (-not (Test-Path $HookDest)) {
        try {
            $hookContent = "@echo off`r`n$VenvPython `"$HookScript`" %*"
            Set-Content -Path $HookDest -Value $hookContent -Encoding ASCII
            Write-Pass "PII git pre-commit hook installed."
        } catch {
            Write-Warn "Could not install git hook — run 'make pii-scan' manually before commits."
        }
    } else {
        Write-Pass "Git hook already present."
    }
} else {
    Write-Warn ".git directory not found — skipping hook installation (not a git repo?)."
}

# ── [5/5] Demo & wizard offer ─────────────────────────────────────────────────
Write-Step "[5/5] Running demo briefing..."
Write-Host ""

& $VenvPython (Join-Path $ScriptDir "scripts\demo_catchup.py")

Write-Host ""
Write-Host "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host "  Artha setup is complete!                                       " -ForegroundColor Green
Write-Host "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host ""
Write-Info "Virtual environment: $VenvPath"
Write-Info "Activate manually:  & '$VenvActivate'"
Write-Host ""
Write-Host "  Next step: open your AI assistant and say 'catch me up'" -ForegroundColor White
Write-Host ""

$answer = Read-Host "  Run the 2-minute setup wizard now? [yes/no]"
if ($answer.Trim().ToLower() -in @("yes", "y")) {
    & $VenvPython (Join-Path $ScriptDir "artha.py") --setup
} else {
    Write-Host ""
    Write-Host "  Run 'python artha.py --setup' whenever you're ready." -ForegroundColor Yellow
    Write-Host "  Or open your AI CLI and say: catch me up" -ForegroundColor Yellow
    Write-Host ""
}
