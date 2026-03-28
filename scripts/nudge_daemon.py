#!/usr/bin/env python3
"""
scripts/nudge_daemon.py — Proactive Time-Aware Nudge Daemon (E4).

Runs as a lightweight standalone check invoked by the vault-watchdog LaunchAgent
between catch-up sessions. Reads ONLY non-encrypted state file frontmatter and
sends time-sensitive nudges via channel_push.py's adapter.

Invocation (from vault-watchdog bash bridge):
    python3 scripts/nudge_daemon.py --check-once

Nudge types:
  overdue_item     — OI-NNN.deadline < today, status=open          🔴 Critical
  today_deadline   — OI-NNN.deadline == today, status=open         🟠 Urgent
  imminent_event   — calendar event starts within 2 hours          🟡 Standard
  catchup_reminder — last_catch_up > 24 hours ago                  🔵 Info
  bill_due_today   — bill_due_tracker.due_date == today            🟠 Urgent

Frequency caps:
  - Max 3 nudges per day (prevent notification fatigue)
  - Min 2 hours between nudges
  - Nudge dedup: tmp/nudge_{type}_{domain}_{YYYYMMDD}.marker

Privacy invariants:
  - NEVER reads vault-encrypted files (immigration, finance, health, insurance, etc.)
  - PII guard on every outbound message
  - Messages are generic: "2 items due today" — never "Chase Visa $3,421"
  - Audit: NUDGE_SENT events logged to state/audit.md

Config flag: enhancements.nudge_daemon (default: true)
Disable: config/channels.yaml nudge_enabled: false

Ref: specs/act-reloaded.md Enhancement 4
"""
from __future__ import annotations

import argparse
try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
import socket
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from context_offloader import load_harness_flag as _load_flag
except ImportError:  # pragma: no cover
    def _load_flag(path: str, default: bool = True) -> bool:  # type: ignore[misc]
        return default

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_NUDGES_PER_DAY = 3
_MIN_HOURS_BETWEEN_NUDGES = 2
_MARKER_TTL_HOURS = 24
_CATCHUP_REMINDER_THRESHOLD_HOURS = 24
_IMMINENT_EVENT_THRESHOLD_HOURS = 2

# Files the nudge daemon is ALLOWED to read (non-encrypted only)
_SAFE_STATE_FILES = frozenset([
    "open_items.md",
    "health-check.md",
])

# Urgency level → emoji
_URGENCY_EMOJI = {1: "🔵", 2: "🟡", 3: "🟠", 4: "🔴"}

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class NudgeItem:
    nudge_type: str
    message: str
    urgency: int          # 1=info 2=standard 3=urgent 4=critical
    domain: str
    deadline: str = ""    # ISO date string
    entity_key: str = ""  # opaque dedup key (OI-NNN or event-id) — not PII


# ---------------------------------------------------------------------------
# Marker file helpers (dedup)
# ---------------------------------------------------------------------------

def _marker_path(nudge_type: str, domain: str, day: date, tmp_dir: Path) -> Path:
    key = f"nudge_{nudge_type}_{domain}_{day.strftime('%Y%m%d')}"
    return tmp_dir / f"{key}.marker"


def _prune_stale_markers(tmp_dir: Path) -> None:
    """Delete marker files older than MARKER_TTL_HOURS."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=_MARKER_TTL_HOURS)
    for p in tmp_dir.glob("nudge_*.marker"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                p.unlink(missing_ok=True)
        except OSError:
            pass


def _is_deduped(nudge_type: str, domain: str, tmp_dir: Path) -> bool:
    today = date.today()
    return _marker_path(nudge_type, domain, today, tmp_dir).exists()


def _mark_sent(nudge_type: str, domain: str, tmp_dir: Path) -> None:
    today = date.today()
    marker = _marker_path(nudge_type, domain, today, tmp_dir)
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text(datetime.now(tz=timezone.utc).isoformat(), encoding="utf-8")
    except OSError:
        pass


def _count_sent_today(tmp_dir: Path) -> int:
    today = date.today().strftime("%Y%m%d")
    return sum(1 for p in tmp_dir.glob(f"nudge_*_{today}.marker"))


def _last_sent_time(tmp_dir: Path) -> datetime | None:
    """Return mtime of the most recently created nudge marker today."""
    today = date.today().strftime("%Y%m%d")
    markers = list(tmp_dir.glob(f"nudge_*_{today}.marker"))
    if not markers:
        return None
    try:
        youngest = max(markers, key=lambda p: p.stat().st_mtime)
        return datetime.fromtimestamp(youngest.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# State file parsers (frontmatter-only, no full body scan)
# ---------------------------------------------------------------------------

def _parse_frontmatter(path: Path) -> dict:
    """Parse YAML frontmatter from a Markdown file."""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    lines = text.splitlines()
    fm_lines: list[str] = []
    in_fm = False
    dash_count = 0
    for line in lines:
        if line.strip() == "---":
            dash_count += 1
            if dash_count == 1:
                in_fm = True
                continue
            if dash_count == 2:
                break
        elif in_fm:
            fm_lines.append(line)
    if not fm_lines:
        return {}
    try:
        return yaml.safe_load("\n".join(fm_lines)) or {}
    except yaml.YAMLError:
        return {}


def _parse_open_items(state_dir: Path) -> list[dict]:
    """Parse open items from state/open_items.md YAML frontmatter."""
    path = state_dir / "open_items.md"
    fm = _parse_frontmatter(path)
    items = fm.get("items", [])
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]


def _parse_last_catchup(state_dir: Path) -> datetime | None:
    """Parse last_catch_up timestamp from health-check.md frontmatter."""
    path = state_dir / "health-check.md"
    fm = _parse_frontmatter(path)
    raw = fm.get("last_catch_up")
    if not raw or str(raw).strip().lower() in ("never", "none", "null", ""):
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    from datetime import datetime as _dt
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y"):
        try:
            return _dt.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Nudge checkers
# ---------------------------------------------------------------------------

def _check_open_items(state_dir: Path) -> list[NudgeItem]:
    """Emit overdue_item and today_deadline nudges from open_items.md."""
    items = _parse_open_items(state_dir)
    today = date.today()
    nudges: list[NudgeItem] = []

    overdue: list[str] = []
    due_today: list[str] = []

    for item in items:
        if str(item.get("status", "open")).lower() not in ("open", "in_progress", "pending"):
            continue
        deadline_str = item.get("deadline")
        if not deadline_str:
            continue
        dl = _parse_date(deadline_str)
        if dl is None:
            continue
        item_id = str(item.get("id", ""))[:10]
        if dl < today:
            overdue.append(item_id)
        elif dl == today:
            due_today.append(item_id)

    if overdue:
        nudges.append(NudgeItem(
            nudge_type="overdue_item",
            message=f"🔴 {len(overdue)} overdue item(s) past deadline. Review open items.",
            urgency=4,
            domain="comms",
            entity_key=",".join(overdue[:5]),
        ))

    if due_today:
        nudges.append(NudgeItem(
            nudge_type="today_deadline",
            message=f"🟠 {len(due_today)} item(s) due today. Act before end of day.",
            urgency=3,
            domain="comms",
            entity_key=",".join(due_today[:5]),
        ))

    return nudges


def _check_catchup_reminder(state_dir: Path) -> list[NudgeItem]:
    """Emit catchup_reminder if last catch-up was >24h ago."""
    last = _parse_last_catchup(state_dir)
    if last is None:
        return []  # No history — don't nag new users
    elapsed = datetime.now(tz=timezone.utc) - last
    if elapsed > timedelta(hours=_CATCHUP_REMINDER_THRESHOLD_HOURS):
        hours = int(elapsed.total_seconds() // 3600)
        return [NudgeItem(
            nudge_type="catchup_reminder",
            message=f"🔵 Artha hasn't run a catch-up in {hours}h. Run /catch-up to stay current.",
            urgency=1,
            domain="meta",
            entity_key="catchup_reminder",
        )]
    return []


def _check_content_moments(artha_dir: Path) -> list[NudgeItem]:
    """Emit content_moment nudge when a high-scoring PR Manager moment exists.

    Priority: LOWEST (urgency=1). Only fires when nudge budget has headroom
    after higher-priority types (goal_stale, relationship_stale, coaching) are processed.

    Reads: tmp/content_moments.json (written by pr_manager.py --step8)
    Gate: enhancements.pr_manager must be truthy in artha_config.yaml.
    Cap: 1 per day (shared under global 3/day cap).

    Ref: specs/pr-manager.md §6.2
    """
    # Check feature flag
    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        cfg = load_config("artha_config", str(artha_dir / "config"))
        pr_flag = cfg.get("enhancements", {}).get("pr_manager", False)
        if isinstance(pr_flag, dict):
            enabled = bool(pr_flag.get("enabled", False))
        else:
            enabled = bool(pr_flag)
        if not enabled:
            return []
    except Exception:  # noqa: BLE001
        return []

    # Read scored moments from cache (written by Step 8)
    moments_file = artha_dir / "tmp" / "content_moments.json"
    if not moments_file.exists():
        return []

    try:
        import json
        moments = json.loads(moments_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []

    if not isinstance(moments, list) or not moments:
        return []

    # Only nudge for moments above daily threshold (score >= 0.8)
    high_moments = [m for m in moments if m.get("above_daily_threshold", False)]
    if not high_moments:
        return []

    # Check if cache is stale (> 24h old — only nudge on fresh data from catch-up)
    try:
        import os, time
        mtime = moments_file.stat().st_mtime
        age_hours = (time.time() - mtime) / 3600
        if age_hours > 24:
            return []
    except Exception:  # noqa: BLE001
        pass

    top = high_moments[0]
    label = str(top.get("label", "a cultural moment"))[:40]  # truncate for safety
    # Sanitize: remove any PII-like patterns just in case
    # (label comes from occasion_tracker — should be festival names only, but be safe)
    label = label.replace("@", "").replace("http", "")
    platforms = top.get("platforms", ["linkedin"])
    platform = platforms[0] if platforms else "linkedin"

    return [NudgeItem(
        nudge_type="content_moment",
        message=(
            f"📣 Content moment: {label} (score {top.get('convergence_score', 0):.2f}). "
            f"Draft a {platform} post? Use /pr draft {platform}"
        ),
        urgency=1,       # Lowest priority — yields to all other nudge types
        domain="social",
        entity_key=f"content_moment_{label[:20].replace(' ', '_')}",
    )]


def check_nudges(artha_dir: Path) -> list[NudgeItem]:
    """Lightweight state file scan for time-sensitive nudges.

    Only reads non-encrypted state files. Returns list of NudgeItem
    sorted by urgency descending (highest priority first).

    Priority ordering (highest → lowest):
      urgency=4: overdue_item         — direct obligation
      urgency=3: today_deadline       — direct obligation (imminent)
      urgency=2: (future: bill_due_today, imminent_event)
      urgency=1: catchup_reminder, content_moment  — informational / nice-to-have
      → content_moment is always LAST within urgency=1 tier
    """
    state_dir = artha_dir / "state"
    nudges: list[NudgeItem] = []
    nudges.extend(_check_open_items(state_dir))
    nudges.extend(_check_catchup_reminder(state_dir))
    # content_moment: lowest priority — only fires when budget headroom remains
    nudges.extend(_check_content_moments(artha_dir))
    # Stable sort: urgency descending, content_moment last within urgency=1
    nudges.sort(
        key=lambda n: (
            -n.urgency,
            1 if n.nudge_type == "content_moment" else 0,
        )
    )
    return nudges


# ---------------------------------------------------------------------------
# Audit logging (with flock for concurrent safety)
# ---------------------------------------------------------------------------

def _audit_log(artha_dir: Path, message: str) -> None:
    audit_path = artha_dir / "state" / "audit.md"
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = f"\n- [{ts}] NUDGE_SENT: {message}"
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "a", encoding="utf-8") as fh:
            if fcntl is not None:
                fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(entry)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Channel dispatch
# ---------------------------------------------------------------------------

def _load_channels_config(artha_dir: Path) -> dict:
    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        return load_config("channels")
    except Exception:
        return {}


def _nudge_enabled(channels_config: dict) -> bool:
    return bool(channels_config.get("nudge_enabled", True))


def send_nudge(nudge: NudgeItem, channels_config: dict, artha_dir: Path) -> bool:
    """Dispatch a single nudge via the channel_push adapter.

    Returns True if dispatched, False if skipped (config, PII, or adapter error).
    """
    if not _nudge_enabled(channels_config):
        return False

    # PII guard — message must not contain known PII patterns
    # (message is generated by this module from aggregate counts only, so this
    # is a defence-in-depth check)
    pii_patterns = [
        r"\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b",   # email address
        r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b",   # US phone
        r"\b\d{4}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4}\b",  # card number pattern
    ]
    import re
    for pat in pii_patterns:
        if re.search(pat, nudge.message):
            return False  # Refuse to send message with PII

    # Attempt to reuse channel_push adapter
    try:
        from channel_push import push_message  # type: ignore[import]
        push_message(nudge.message, scope="personal")
        return True
    except (ImportError, Exception):  # noqa: BLE001
        # Silent fail — nudge daemon must never crash
        return False


# ---------------------------------------------------------------------------
# Main check loop
# ---------------------------------------------------------------------------

def _verify_nudge_host(channels_config: dict) -> bool:
    """Check this machine is the designated listener host.

    Reads defaults.listener_host from channels.yaml.
    Empty string = any host allowed (single-machine mode).
    """
    designated = channels_config.get("defaults", {}).get("listener_host", "").strip()
    if not designated:
        return True  # single-machine mode — run anywhere
    return socket.gethostname().lower() == designated.lower()


def run_check_once(artha_dir: Path) -> int:
    """Perform one nudge check cycle. Called by vault-watchdog bridge.

    Returns 0 always (daemon must not crash watchdog).
    """
    if not _load_flag("enhancements.nudge_daemon", default=True):
        return 0

    # Host gating: only run on the designated listener host
    channels_config = _load_channels_config(artha_dir)
    if not _verify_nudge_host(channels_config):
        return 0

    tmp_dir = artha_dir / "tmp"
    try:
        _prune_stale_markers(tmp_dir)
    except Exception:  # noqa: BLE001
        pass

    # Daily cap check
    if _count_sent_today(tmp_dir) >= _MAX_NUDGES_PER_DAY:
        return 0

    # Minimum gap between nudges
    last_sent = _last_sent_time(tmp_dir)
    if last_sent is not None:
        elapsed = datetime.now(tz=timezone.utc) - last_sent
        if elapsed < timedelta(hours=_MIN_HOURS_BETWEEN_NUDGES):
            return 0

    nudges = check_nudges(artha_dir)

    for nudge in nudges:
        if _count_sent_today(tmp_dir) >= _MAX_NUDGES_PER_DAY:
            break

        if _is_deduped(nudge.nudge_type, nudge.domain, tmp_dir):
            continue

        sent = send_nudge(nudge, channels_config, artha_dir)
        if sent:
            _mark_sent(nudge.nudge_type, nudge.domain, tmp_dir)
            _audit_log(artha_dir, f"{nudge.nudge_type} domain={nudge.domain}")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Artha nudge daemon — proactive time-aware notifications"
    )
    parser.add_argument(
        "--check-once",
        action="store_true",
        help="Run one check cycle then exit (used by vault-watchdog bridge)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print nudges to stdout without sending",
    )
    parser.add_argument(
        "--artha-dir",
        type=Path,
        default=_ROOT_DIR,
        help="Root Artha directory (default: auto-detected)",
    )
    args = parser.parse_args()

    artha_dir = args.artha_dir

    if args.dry_run:
        nudges = check_nudges(artha_dir)
        print(f"Nudge check: {len(nudges)} potential nudge(s)")
        for n in nudges:
            print(f"  [{n.nudge_type}] urgency={n.urgency} {n.message}")
        return 0

    if args.check_once:
        return run_check_once(artha_dir)

    # Default: print status
    tmp_dir = artha_dir / "tmp"
    sent_today = _count_sent_today(tmp_dir)
    print(f"Nudge daemon: {sent_today}/{_MAX_NUDGES_PER_DAY} nudges sent today")
    return 0


if __name__ == "__main__":
    sys.exit(main())
