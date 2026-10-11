"""preflight/footprint_checks.py — no background footprint (re-artha §1.4, §6.12, WP-01).

Artha does work only inside a session the user starts.  This check inventories
what would contradict that: scheduler entries (launchd, systemd timers, cron,
Windows Scheduled Tasks) and listening processes that belong to Artha.  The one
documented exception is the vault watchdog, retained until the in-memory vault
passes crash tests (§6.11 phase 4).  A tool that cannot be queried is reported,
never treated as "nothing found".
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

from preflight._types import CheckResult

Runner = Callable[[list[str]], tuple[int, str]]

_ALLOWED = ("vault-watchdog", "vaultwatchdog", "vault_watchdog")
_ARTHA_RE = re.compile(r"artha", re.IGNORECASE)
_DAEMON_SCRIPTS = re.compile(
    r"(channel_listener|artha_engine|nudge_daemon|agent_scheduler|vault_watchdog)\.py", re.IGNORECASE)


def _default_runner(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    return proc.returncode, proc.stdout


def _is_allowed(text: str) -> bool:
    low = text.lower()
    return any(a in low for a in _ALLOWED)


def check_background_footprint(
    runner: Runner | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> CheckResult:
    runner = runner or _default_runner
    platform = platform or sys.platform
    home = Path(home) if home else Path.home()
    name = "Background footprint"
    found: list[str] = []
    allowed: list[str] = []
    unknown: list[str] = []

    def run(argv: list[str]) -> str | None:
        try:
            code, out = runner(argv)
        except (OSError, subprocess.SubprocessError):
            unknown.append(argv[0])
            return None
        return out if code == 0 else ""

    def classify(item: str) -> None:
        (allowed if _is_allowed(item) else found).append(item)

    if platform == "darwin":
        out = run(["launchctl", "list"])
        for line in (out or "").splitlines():
            label = line.split("\t")[-1].strip()
            if _ARTHA_RE.search(label):
                classify(f"launchd job {label}")
        agents = home / "Library" / "LaunchAgents"
        if agents.is_dir():
            for plist in sorted(agents.glob("*.plist")):
                if _ARTHA_RE.search(plist.name):
                    classify(f"installed definition {plist.name}")
    elif platform.startswith("linux"):
        out = run(["systemctl", "--user", "list-timers", "--all", "--no-pager"])
        for line in (out or "").splitlines():
            for token in line.split():
                if _ARTHA_RE.search(token) and token.endswith((".timer", ".service")):
                    classify(f"systemd {token}")
    elif platform == "win32":
        out = run(["schtasks", "/Query", "/FO", "LIST"])
        for line in (out or "").splitlines():
            if line.lower().startswith("taskname:") and _ARTHA_RE.search(line):
                classify(f"scheduled task {line.split(':', 1)[1].strip().lstrip(chr(92))}")

    if platform != "win32":
        cron = run(["crontab", "-l"])
        for line in (cron or "").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and _ARTHA_RE.search(line):
                classify(f"cron entry {line.strip()[:60]}")
        listen = run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"])
        if listen and "LISTEN" in listen:
            procs = run(["ps", "-axo", "pid=,command="]) or ""
            for line in procs.splitlines():
                m = _DAEMON_SCRIPTS.search(line)
                if m:
                    classify(f"running process {m.group(1)}.py")

    if unknown:
        return CheckResult(
            name, "P1", False,
            "could not query: " + ", ".join(sorted(set(unknown))) + " (footprint unverified)",
            fix_hint="Run the check from a normal terminal on the authority host.",
        )
    if found:
        return CheckResult(
            name, "P1", False, "; ".join(found),
            fix_hint="Artha must not own scheduled jobs or listeners (PULL over PUSH). "
                     "Unload and remove them, e.g. `launchctl bootout gui/$UID <plist>` then delete the file.",
        )
    note = ""
    if allowed:
        note = " — documented exception until Stage 1: " + "; ".join(allowed) + " (vault watchdog)"
    return CheckResult(name, "P1", True, "no scheduler entries or listeners owned by Artha" + note)
