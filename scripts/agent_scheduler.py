# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/agent_scheduler.py — Scheduled pre-computation for agent fleet (EAR-3).

Reads schedules.yaml, evaluates cron-style expressions against last-run state,
and invokes agent_manager.py delegate for each due agent.

Schedule definition: config/agents/schedules.yaml
State: tmp/ext-agent-schedule-state.yaml

Execution:
  python scripts/agent_scheduler.py --tick
    → Idempotent. Runs all past-due schedules. Safe to call repeatedly.
  python scripts/agent_scheduler.py --status
    → Show schedule state for all configured agents.

Safety constraints:
  - Budget cap: max _MAX_SCHEDULED_AGENTS (8) scheduled agent slots.
    Agents sharing a cron_group occupy one slot; ungrouped agents each occupy one slot.
  - Cooldown: no agent scheduled more than 4×/day.
  - Stale schedule detection: 3 consecutive failures → suspend + surface in health.
  - No auto-install of OS-level schedulers.  User creates the cron/task manually.
  - All invocations still run through the full AR-9 pipeline.
  - Staleness tolerance: pre-computed caches are time-stamped; responses
    served with "⚠️ data may be stale (cached N hours ago)" if beyond tolerance.

Cron expression: stdlib-only parser.
  Supports: *, specific values, ranges (1-5), lists (0,30).
  Step values (*/15) deferred to V2.1.

Ref: specs/ext-agent-reloaded.md §EAR-3
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _REPO_ROOT / "config"
_SCHEDULES_FILE = _CONFIG_DIR / "agents" / "schedules.yaml"
_STATE_FILE = _REPO_ROOT / "tmp" / "ext-agent-schedule-state.yaml"
_SCRIPTS_DIR = _REPO_ROOT / "scripts"

_MAX_SCHEDULED_AGENTS = 8
_MAX_RUNS_PER_DAY = 4
_FAIL_SUSPEND_THRESHOLD = 3

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Cron parser (stdlib-only)
# ---------------------------------------------------------------------------

def _parse_cron_field(field_str: str, field_min: int, field_max: int) -> set[int]:
    """Parse a cron field string into a set of matching integer values."""
    if field_str == "*":
        return set(range(field_min, field_max + 1))

    values: set[int] = set()
    for part in field_str.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            values.update(range(int(lo), int(hi) + 1))
        else:
            values.add(int(part))
    return values


def _cron_matches(cron_expr: str, dt: datetime) -> bool:
    """Return True if datetime dt matches the cron_expr.

    Format: minute hour day-of-month month day-of-week
    Supports: *, specific, ranges (n-m), lists (a,b,c).
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return False

    minute_set    = _parse_cron_field(parts[0], 0, 59)
    hour_set      = _parse_cron_field(parts[1], 0, 23)
    dom_set       = _parse_cron_field(parts[2], 1, 31)
    month_set     = _parse_cron_field(parts[3], 1, 12)
    dow_set       = _parse_cron_field(parts[4], 0, 6)

    return (
        dt.minute in minute_set
        and dt.hour in hour_set
        and dt.day in dom_set
        and dt.month in month_set
        and dt.weekday() in dow_set  # Python weekday: 0=Mon, 6=Sun
    )


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    try:
        if _STATE_FILE.exists():
            import yaml  # noqa: PLC0415
            return yaml.safe_load(_STATE_FILE.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


def _save_state(state: dict) -> None:
    try:
        import yaml  # noqa: PLC0415
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=_STATE_FILE.parent, prefix=".sched_tmp_", suffix=".yaml"
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            yaml.dump(state, fh, allow_unicode=True)
        os.replace(tmp_path, _STATE_FILE)
    except Exception:
        pass


def _load_schedules() -> list[dict]:
    try:
        if not _SCHEDULES_FILE.exists():
            return []
        import yaml  # noqa: PLC0415
        data = yaml.safe_load(_SCHEDULES_FILE.read_text(encoding="utf-8")) or {}
        return data.get("schedules", []) or []
    except Exception:
        return []


def _check_preconditions(sched: dict) -> tuple[bool, str]:
    """Check any preconditions listed in a schedule entry before running the agent.

    RD-03: Supports ``vault_plaintext_available: <domain>`` preconditions.
    Returns (ok, reason). If ok=False, the scheduler skips the agent and logs
    the reason.
    """
    preconditions = sched.get("preconditions", [])
    if not preconditions:
        return True, ""

    for pc in preconditions:
        if not isinstance(pc, dict):
            continue
        domain = pc.get("vault_plaintext_available")
        if domain:
            plain = _REPO_ROOT / "state" / f"{domain}.md"
            age   = _REPO_ROOT / "state" / f"{domain}.md.age"
            if age.exists() and not plain.exists():
                return False, f"vault_locked:{domain}"

    return True, ""


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def _count_runs_today(state_entry: dict) -> int:
    """Count how many times this agent was run today."""
    today = datetime.now(timezone.utc).date().isoformat()
    runs = state_entry.get("runs_today", {})
    return runs.get(today, 0)


def _tick(dry_run: bool = False) -> int:
    """Check all schedules and run past-due ones.

    Returns count of agents run.
    """
    import json as _json  # noqa: PLC0415

    schedules = _load_schedules()
    if not schedules:
        print("No schedules configured in config/agents/schedules.yaml")
        return 0

    # Cap: cron_group-aware slot counting.
    # Agents sharing a cron_group occupy ONE slot; ungrouped agents each occupy one slot.
    seen_slots: dict[str, list[dict]] = {}
    for _s in schedules:
        _slot_key = _s.get("cron_group") or _s.get("agent", "")
        seen_slots.setdefault(_slot_key, []).append(_s)

    # DEBT-022: Emit visible warning when slot cap is exceeded (silent truncation → fail-loud)
    if len(seen_slots) > _MAX_SCHEDULED_AGENTS:
        _dropped_slots = list(seen_slots.keys())[_MAX_SCHEDULED_AGENTS:]
        _dropped_agents = [
            agent.get("agent", slot)
            for slot in _dropped_slots
            for agent in seen_slots[slot]
        ]
        _cap_msg = (
            f"[WARNING] Agent slot cap ({_MAX_SCHEDULED_AGENTS}) exceeded. "
            f"{len(_dropped_slots)} slot(s) dropped: {_dropped_agents}"
        )
        print(_cap_msg, file=sys.stderr)
        _log(f"AGENT_SLOT_CAP_EXCEEDED | cap:{_MAX_SCHEDULED_AGENTS} | dropped:{_dropped_agents}")

    slot_keys = list(seen_slots.keys())[:_MAX_SCHEDULED_AGENTS]
    schedules_by_slot = [seen_slots[k] for k in slot_keys]

    state = _load_state()
    now = datetime.now(timezone.utc)
    ran = 0

    for slot_agents in schedules_by_slot:
        for sched in slot_agents:
            agent_name = sched.get("agent", "")
            cron = sched.get("cron", "")
            query = sched.get("query", "")
            staleness_tolerance = sched.get("staleness_tolerance_seconds", 3600)  # noqa: F841

            if not agent_name or not cron or not query:
                continue

            entry = state.setdefault(agent_name, {
                "last_run": None,
                "last_result": None,
                "consecutive_failures": 0,
                "suspended": False,
                "runs_today": {},
            })

            # Skip suspended schedules
            if entry.get("suspended"):
                print(f"  ⏸ {agent_name}: schedule suspended (>={_FAIL_SUSPEND_THRESHOLD} failures)")
                continue

            # Cooldown: max runs per day
            if _count_runs_today(entry) >= _MAX_RUNS_PER_DAY:
                print(f"  🚫 {agent_name}: daily run limit reached ({_MAX_RUNS_PER_DAY})")
                continue

            # Check if this schedule's cron expression matches now (or is overdue)
            last_run_str = entry.get("last_run")
            is_due = False

            if last_run_str:
                try:
                    datetime.fromisoformat(last_run_str.replace("Z", "+00:00"))
                    # Due if cron matches current time OR if ≥1 hour since last run
                    # and cron was due since last run (simplified: use 1h granularity)
                    if _cron_matches(cron, now):
                        is_due = True
                except (ValueError, TypeError):
                    is_due = True
            else:
                is_due = True  # Never run before → always due

            if not is_due:
                continue

            # Derive domain name and session trace ID (R5: correlate pre-compute → catch-up)
            domain = agent_name.lower().replace("agent", "").strip()
            session_trace_id = f"pre-compute-{domain}-{now.strftime('%Y-%m-%dT%H-%M-%S')}"

            if dry_run:
                print(f"  [dry-run] Would run: {agent_name} [{session_trace_id}]")
                continue

            # RD-03: Check preconditions before dispatching the agent.
            # This prevents silent data starvation (e.g. CapitalAgent running
            # when vault is locked).
            pc_ok, pc_reason = _check_preconditions(sched)
            if not pc_ok:
                print(
                    f"  ⛔ {agent_name}: precondition failed ({pc_reason}) — skipped",
                    file=sys.stderr,
                )
                _log(f"PRECONDITION_FAILED | agent:{agent_name} | reason:{pc_reason}")
                ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                entry["last_run"] = ts
                entry["last_result"] = f"PRECONDITION_FAILED:{pc_reason}"
                ran += 1  # counts as a scheduling decision (not a failure)
                continue

            # Begin session trace so all log events within this domain run are correlated
            try:
                from lib.logger import begin_session_trace as _begin_session_trace  # noqa: PLC0415
                _begin_session_trace(session_trace_id)
            except (ImportError, Exception):
                pass

            # Execute
            print(f"  ▶ Running scheduled agent: {agent_name} [{session_trace_id}]")
            cmd = [
                sys.executable,
                str(_SCRIPTS_DIR / "agent_manager.py"),
                "delegate",
                "--agent", agent_name,
                "--query", query,
            ]

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=str(_REPO_ROOT),
                )
                success = result.returncode == 0
            except subprocess.TimeoutExpired:
                success = False

            ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            entry["last_run"] = ts
            entry["last_result"] = "success" if success else "failure"

            today = now.date().isoformat()
            if "runs_today" not in entry:
                entry["runs_today"] = {}
            entry["runs_today"][today] = entry["runs_today"].get(today, 0) + 1

            if success:
                entry["consecutive_failures"] = 0
            else:
                entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
                if entry["consecutive_failures"] >= _FAIL_SUSPEND_THRESHOLD:
                    entry["suspended"] = True
                    print(f"  ⛔ {agent_name}: suspended after {_FAIL_SUSPEND_THRESHOLD} failures")

            # Write per-domain heartbeat (R5: session_trace_id links pre-compute → morning briefing)
            # Preserve agent-written records_written if agent updated the file during subprocess run
            _hb_file = _REPO_ROOT / "tmp" / f"{domain}_last_run.json"
            _agent_records = 0
            try:
                _prev_hb = _json.loads(_hb_file.read_text(encoding="utf-8"))
                _agent_records = int(_prev_hb.get("records_written", 0))
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            _heartbeat = {
                "domain": domain,
                "session_trace_id": session_trace_id,
                "timestamp_utc": ts,
                "status": "success" if success else "failure",
                "records_written": _agent_records,
            }
            if success and _agent_records == 0:
                print(
                    f"  \u26a0 {agent_name}: records_written=0 \u2014 agent heartbeat uninitialized "
                    "(agent may not have written its own heartbeat)",
                    file=sys.stderr,
                )
            try:
                _hb_file.parent.mkdir(parents=True, exist_ok=True)
                _hb_file.write_text(_json.dumps(_heartbeat, indent=2), encoding="utf-8")
            except OSError:
                pass

            ran += 1

    if not dry_run:
        _save_state(state)

    # R-12: Weekly metrics digest — aggregate JSONL → ext-agent-health-digest.md
    # Run on each tick (idempotent: digest checks its own staleness internally)
    if not dry_run:
        try:
            from lib.metrics_digest import run_digest  # noqa: PLC0415
            _digest = run_digest(weeks=1)
            print(f"  📊 Metrics digest updated ({_digest.get('total_invocations', 0)} invocations, "
                  f"{_digest.get('agent_count', 0)} agent(s))")
        except (ImportError, Exception) as _de:
            print(f"  ⚠️  Metrics digest skipped: {_de}", file=sys.stderr)

    return ran


def _status() -> None:
    schedules = _load_schedules()
    state = _load_state()

    # DEBT-022: Show slot utilisation in header
    total_slots = len(schedules)
    active_slots = min(total_slots, _MAX_SCHEDULED_AGENTS)
    print(f"\nAgent slots: {active_slots}/{_MAX_SCHEDULED_AGENTS} used"
          + (f"  ⚠ {total_slots - _MAX_SCHEDULED_AGENTS} dropped (cap exceeded)" if total_slots > _MAX_SCHEDULED_AGENTS else ""))
    print(f"\n{'Agent':<35} {'Last Run':<22} {'Result':<10} {'Suspended'}")
    print("─" * 80)
    for sched in schedules[:_MAX_SCHEDULED_AGENTS]:
        name = sched.get("agent", "?")
        entry = state.get(name, {})
        last = entry.get("last_run", "never")
        result = entry.get("last_result", "n/a")
        suspended = "⏸ YES" if entry.get("suspended") else "no"
        print(f"  {name:<33} {last:<22} {result:<10} {suspended}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="EAR-3 Scheduled Pre-Computation for Artha agent fleet"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tick", action="store_true", help="Run due schedules")
    group.add_argument("--status", action="store_true", help="Show schedule state")
    group.add_argument("--dry-run", action="store_true",
                       help="Show which agents would run without executing")
    args = parser.parse_args(argv)

    if args.tick:
        n = _tick(dry_run=False)
        print(f"\nScheduler tick complete: {n} agent(s) run.")
        return 0
    elif args.dry_run:
        _tick(dry_run=True)
        return 0
    elif args.status:
        _status()
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
