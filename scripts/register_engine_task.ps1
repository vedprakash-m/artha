<#
scripts/register_engine_task.ps1
=================================
Registers the ArthaEngine Task Scheduler task — the single-process host that
replaces the old ArthaChannelListener task (ADR-004, Option B).

IMPORTANT: Run this script ONCE, AFTER the old channel_listener.py Task
Scheduler task has finished its last run.  Running artha_engine.py and
channel_listener.py simultaneously violates the rate-limiter singleton
invariant (spec §6.3).

Required triggers (R12 — both are MANDATORY):
  1. AtStartup  — post-reboot recovery (machine was off)
  2. OnFailure  — intraday asyncio event-loop crash recovery
                  delay ≤5 min, max 3 retries per day

Usage:
  # Step 1 — retire the old task:
  schtasks /End /TN "ArthaChannelListener"
  schtasks /Delete /TN "ArthaChannelListener" /F

  # Step 2 — register the engine task:
  powershell -ExecutionPolicy Bypass -File scripts\register_engine_task.ps1

  # Verify:
  schtasks /Query /TN "ArthaEngine" /V /FO LIST

#>

[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$Force   # Skip the "old task already running?" check
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ArthaDir    = Split-Path -Parent $ScriptDir
$EngineScript = Join-Path $ScriptDir "artha_engine.py"

# Resolve Python interpreter (prefer .venv inside Artha directory)
$VenvPython = Join-Path $ArthaDir ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $PythonExe = $VenvPython
} else {
    $PythonExe = (Get-Command python -ErrorAction SilentlyContinue)?.Source
    if (-not $PythonExe) {
        Write-Error "[ArthaEngine] Python not found. Install Python and/or activate the venv."
        exit 1
    }
}

if (-not (Test-Path $EngineScript)) {
    Write-Error "[ArthaEngine] artha_engine.py not found at: $EngineScript"
    exit 1
}

$OldTaskName = "ArthaChannelListener"
$NewTaskName = "ArthaEngine"

# ---------------------------------------------------------------------------
# Safety gate — refuse to register if old task is still enabled
# ---------------------------------------------------------------------------
if (-not $Force) {
    $OldTask = Get-ScheduledTask -TaskName $OldTaskName -ErrorAction SilentlyContinue
    if ($OldTask -and $OldTask.State -ne "Disabled") {
        Write-Warning @"
[ArthaEngine] '$OldTaskName' task still exists and is not disabled.

Running artha_engine.py alongside channel_listener.py creates two independent
rate-limiter instances — a security violation (spec §6.3 / ADR-004).

Disable the old task first:
  schtasks /End    /TN "$OldTaskName"
  schtasks /Change /TN "$OldTaskName" /DISABLE
  schtasks /Delete /TN "$OldTaskName" /F

Then re-run this script, or pass -Force to skip this check.
"@
        exit 2
    }
}

# ---------------------------------------------------------------------------
# Disable old task if it exists and Force was passed
# ---------------------------------------------------------------------------
if ($Force) {
    $OldTask = Get-ScheduledTask -TaskName $OldTaskName -ErrorAction SilentlyContinue
    if ($OldTask) {
        Write-Host "[ArthaEngine] -Force: disabling old task '$OldTaskName'…"
        Disable-ScheduledTask -TaskName $OldTaskName -ErrorAction SilentlyContinue | Out-Null
    }
}

# ---------------------------------------------------------------------------
# Build new task components
# ---------------------------------------------------------------------------
$TaskAction = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$EngineScript`"" `
    -WorkingDirectory $ArthaDir

# Trigger 1: @startup — post-reboot recovery (R12, required)
$StartupTrigger = New-ScheduledTaskTrigger -AtStartup

# Trigger 2: immediate one-shot trigger — starts the task when first registered
# (the AtStartup trigger only fires on next reboot, so also start now)
$ImmediateTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(10)

$TaskSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit        (New-TimeSpan -Hours 23) `
    -RestartCount              3 `
    -RestartInterval           (New-TimeSpan -Minutes 5) `
    -DisallowStartIfOnBatteries $false `
    -StopIfGoingOnBatteries    $false `
    -MultipleInstances         IgnoreNew `
    -StartWhenAvailable        $true

# Note on OnFailure / RestartCount:
# Task Scheduler's "restart on failure" is controlled by RestartCount +
# RestartInterval in New-ScheduledTaskSettingsSet — NOT by a separate trigger.
# RestartCount=3 means up to 3 restarts with 5-minute delays between them (R12).

$TaskPrincipal = New-ScheduledTaskPrincipal `
    -UserId  ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited   # No elevation required — artha_engine.py is user-space

$Task = New-ScheduledTask `
    -Action      $TaskAction `
    -Trigger     @($StartupTrigger, $ImmediateTrigger) `
    -Settings    $TaskSettings `
    -Principal   $TaskPrincipal `
    -Description "Artha Engine — unified Telegram listener + daily scheduler + watchdog (ADR-004 Option B)"

# ---------------------------------------------------------------------------
# Register / replace the task
# ---------------------------------------------------------------------------
Register-ScheduledTask `
    -TaskName   $NewTaskName `
    -InputObject $Task `
    -Force | Out-Null

Write-Host @"
[ArthaEngine] Task '$NewTaskName' registered successfully.

Triggers configured:
  1. AtStartup       — fires after every reboot (post-reboot recovery)
  2. Once (now+10s)  — fires immediately so engine starts without a reboot
  3. RestartCount=3  — Task Scheduler restarts on failure, up to 3 times,
                       with 5-minute intervals (intraday crash recovery)

Verify with:
  schtasks /Query /TN "$NewTaskName" /V /FO LIST

If '$OldTaskName' still exists, remove it:
  schtasks /Delete /TN "$OldTaskName" /F
"@
