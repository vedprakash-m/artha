<#
scripts/vault_watchdog_win.ps1
==============================
Windows Task Scheduler vault watchdog for Artha.

Registers or runs as a per-user Task Scheduler task (no admin required).

Usage:
  # Register the task (run once during setup):
  powershell -ExecutionPolicy Bypass -File scripts\vault_watchdog_win.ps1 -Register

  # Run the watchdog directly (called by the scheduled task):
  powershell -ExecutionPolicy Bypass -File scripts\vault_watchdog_win.ps1

  # Verify registration:
  schtasks /Query /TN "ArthaVaultWatchdog"

Environment variables:
  ARTHA_WATCHDOG_INTERVAL_SECS  override stale-lock TTL for testing

#>

[CmdletBinding()]
param(
    [switch]$Register  # If set, register the Task Scheduler task instead of running
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ArthaDir   = Split-Path -Parent $ScriptDir
$VaultPy    = Join-Path $ScriptDir "vault.py"
$WatchdogPy = Join-Path $ScriptDir "vault_watchdog.py"

# Resolve Python interpreter (prefer .venv inside Artha directory)
$VenvPython = Join-Path $ArthaDir ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $PythonExe = $VenvPython
} else {
    # Fall back to python in PATH
    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue)?.Source
    if (-not $PythonExe) {
        Write-Error "[ArthaWatchdog] Python not found. Install Python and/or activate the venv."
        exit 1
    }
}

# ---------------------------------------------------------------------------
# Register mode — sets up the Task Scheduler job (no admin required)
# ---------------------------------------------------------------------------
if ($Register) {
    $TaskName   = "ArthaVaultWatchdog"
    $TaskAction = New-ScheduledTaskAction `
        -Execute $PythonExe `
        -Argument "`"$WatchdogPy`"" `
        -WorkingDirectory $ArthaDir

    $TaskTrigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 2) -Once `
        -At (Get-Date).AddMinutes(1)

    $TaskSettings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
        -RestartCount 0 `
        -DisallowStartIfOnBatteries $false `
        -StopIfGoingOnBatteries $false

    $TaskPrincipal = New-ScheduledTaskPrincipal `
        -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
        -LogonType Interactive `
        -RunLevel Limited  # No elevation required

    $Task = New-ScheduledTask `
        -Action $TaskAction `
        -Trigger $TaskTrigger `
        -Settings $TaskSettings `
        -Principal $TaskPrincipal `
        -Description "Artha vault watchdog — re-encrypts stale plaintext vault files after abnormal exit"

    Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null
    Write-Host "[ArthaWatchdog] Task '$TaskName' registered. Verify with: schtasks /Query /TN '$TaskName'"
    exit 0
}

# ---------------------------------------------------------------------------
# Watchdog run mode — delegate to vault_watchdog.py
# ---------------------------------------------------------------------------
if (-not (Test-Path $WatchdogPy)) {
    Write-Error "[ArthaWatchdog] vault_watchdog.py not found at: $WatchdogPy"
    exit 1
}

$env:PYTHONPATH = "$ArthaDir;$ScriptDir;$($env:PYTHONPATH)"
& $PythonExe $WatchdogPy
exit $LASTEXITCODE
