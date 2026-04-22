#!/usr/bin/env python3
"""export_hermes_context.py — Write Artha life context to sensor.artha_context in HA.

Runs as:
  1. Post-pipeline hook — called from pipeline.py after Step 21 (Persistent Fact
     Extraction). Import: ``from export_hermes_context import export_hermes_context``
  2. Standalone periodic refresh via Mac LaunchAgent every 4 hours.
     CLI: ``python3 scripts/export_hermes_context.py --standalone``

Flags:
  --standalone  Periodic refresh mode. Checks pipeline sentinel before reading
                state files. Skips pipeline audit log write.
  --dry-run     Build and log the payload but do NOT POST to Home Assistant.

Privacy policy (specs/h-int.md §6, §16):
  - Only Personal OS data from allowlisted source_domain values is exported.
  - Work OS data (state/work/*, employment, WorkIQ, M365) is never exported.
  - Sensitive personal data (immigration, financial accounts, medical) is excluded
    via domain allowlist (primary guard) and work-signal scan (defense-in-depth).

Ref: specs/h-int.md FR-HI Phase 1, §7.1–§7.3, §16.
"""
from __future__ import annotations

import argparse
import json
import logging
import platform
import re
import socket
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import keyring
import requests
import yaml

from lib.config_loader import load_config

log = logging.getLogger("export_hermes_context")

# ---------------------------------------------------------------------------
# Repo root (scripts/ → parent)
# ---------------------------------------------------------------------------
_ARTHA_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Work OS — blocked calendar sources (must stay in sync with calendar_writer.py)
# ---------------------------------------------------------------------------
_BLOCKED_CALENDAR_SOURCES = frozenset({"msgraph_calendar", "workiq_calendar"})

# Calendar sources that are explicitly personal (allowlist)
_PERSONAL_CALENDAR_SOURCES = frozenset({
    "google_calendar",
    "gcal",
    "icloud_calendar",
    "caldav_calendar",
    "outlook_calendar",
    "manual",
    "parentsquare_email",
})

# Goal categories allowed for export (personal only — not career/corporate)
_ALLOWED_GOAL_CATEGORIES = frozenset({
    "fitness", "health", "learning", "personal", "wellness", "academic",
})

# Work OS signal words for defense-in-depth scan (§16.4)
_WORK_SIGNALS = [
    "workiq",
    r"\bado\b",
    r"\bsprint\b",
    "incident",
    r"\bicm\b",
    r"teams\.microsoft",
    "work_os",
    r"\bemployment\b",
    "performance",
    "calibration",
]

# ---------------------------------------------------------------------------
# Allowlist loading
# ---------------------------------------------------------------------------

def _load_allowed_domains(artha_dir: Path) -> frozenset[str]:
    """Load the source_domain allowlist from config/hermes_context_allowlist.yaml."""
    allowlist_path = artha_dir / "config" / "hermes_context_allowlist.yaml"
    try:
        cfg = yaml.safe_load(allowlist_path.read_text(encoding="utf-8")) or {}
        return frozenset(cfg.get("allowed_domains", []))
    except FileNotFoundError:
        log.warning(
            "hermes_context_allowlist.yaml not found — using hardcoded defaults"
        )
        return frozenset({
            "finance", "insurance", "health", "kids", "home", "digital",
            "travel", "learning", "shopping", "wellness", "social", "calendar",
        })


# ---------------------------------------------------------------------------
# State readers
# ---------------------------------------------------------------------------

def _read_goals(goals_path: Path, status_filter: str) -> list[str]:
    """Return titles of goals matching *status_filter* from state/goals.md.

    Parses the YAML list body after the frontmatter. Excludes goals whose
    category is not in _ALLOWED_GOAL_CATEGORIES (e.g. career).
    """
    try:
        text = goals_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []

    # Goals live inside the YAML frontmatter (between the two --- markers),
    # not in the markdown body that follows. Parse parts[1] (the frontmatter),
    # not parts[2] (the markdown body which has no 'goals' key).
    if text.startswith("---"):
        parts = text.split("---", 2)
        frontmatter = parts[1] if len(parts) >= 2 else text
    else:
        frontmatter = text

    try:
        data = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        return []

    result: list[str] = []
    for g in (data.get("goals") or []):
        if not isinstance(g, dict):
            continue
        if g.get("status") != status_filter:
            continue
        if g.get("category", "") not in _ALLOWED_GOAL_CATEGORIES:
            continue
        title = str(g.get("title", "")).strip()
        if title:
            result.append(title)
    return result


def _read_open_items(
    items_path: Path,
    priority: str,
    allowed_domains: frozenset[str],
) -> list[str]:
    """Return open item descriptions for a given priority, filtered by domain.

    Uses the same regex-based field extraction pattern as todo_sync.py to
    handle the mixed Markdown/YAML format with embedded colons and pipes.
    """
    try:
        content = items_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []

    block_pattern = re.compile(
        r"^- id:\s*(.+?)$(.+?)(?=^- id:|\Z|^##)",
        re.MULTILINE | re.DOTALL,
    )

    result: list[str] = []
    for m in block_pattern.finditer(content):
        block = m.group(2)

        def _field(name: str) -> str:
            fm = re.search(rf"^\s+{name}:\s*(.+)$", block, re.MULTILINE)
            if not fm:
                return ""
            val = fm.group(1).strip()
            # Strip YAML-style quotes
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]
            return val

        if _field("status") != "open":
            continue
        if _field("priority") != priority:
            continue
        domain = _field("source_domain")
        if domain not in allowed_domains:
            continue
        desc = _field("description")
        if desc:
            result.append(desc[:120] if len(desc) > 120 else desc)

    return result


def _parse_event_date(date_str: str) -> tuple[Optional[date], str]:
    """Parse a calendar event date string into (date_obj, time_str).

    time_str is human-friendly: "3pm", "4:30pm", "Wed", or "".
    Returns (None, "") if unparseable.
    """
    if not date_str:
        return None, ""

    # Range like "2026-03-22T15:00:00Z → 2026-03-22T17:00:00Z" — take start
    if "→" in date_str:
        date_str = date_str.split("→")[0].strip()

    # Remove parenthetical annotations like "(Wednesday)"
    date_str = re.sub(r"\([^)]*\)", "", date_str).strip()

    # ISO datetime: "2026-04-07T16:30:00Z" or "2026-04-07T16:30:00"
    dt_match = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})", date_str)
    if dt_match:
        try:
            d = date.fromisoformat(dt_match.group(1))
            hour = int(dt_match.group(2))
            minute = int(dt_match.group(3))
            ampm = "am" if hour < 12 else "pm"
            hour12 = hour % 12 or 12
            time_str = (
                f"{hour12}{ampm}" if minute == 0 else f"{hour12}:{minute:02d}{ampm}"
            )
            return d, time_str
        except ValueError:
            pass

    # Plain date: "2026-04-08"
    plain_match = re.match(r"(\d{4}-\d{2}-\d{2})", date_str)
    if plain_match:
        try:
            d = date.fromisoformat(plain_match.group(1))
            # For all-day events, use abbreviated day name as time hint
            time_str = d.strftime("%a")
            return d, time_str
        except ValueError:
            pass

    return None, ""


def _read_upcoming_events(artha_dir: Path, days: int = 7) -> list[dict]:
    """Parse state/calendar.md for upcoming events within ``days`` days.

    Returns list of dicts: {title: str, time: str, today: bool}.
    Blocked calendar sources (Work OS) are excluded.
    state/calendar.md may not exist — returns [] gracefully.
    """
    calendar_path = artha_dir / "state" / "calendar.md"
    try:
        content = calendar_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []

    today_d = date.today()
    cutoff_ordinal = today_d.toordinal() + days

    # Event block: "- **Title**  <!-- ... -->" then indented "  - Attr: val" lines
    event_pattern = re.compile(
        r"^- \*\*(.+?)\*\*.*?\n((?:  - .+\n?)*)",
        re.MULTILINE,
    )

    results: list[dict] = []
    for m in event_pattern.finditer(content):
        title = m.group(1).strip()
        attrs = m.group(2)

        # Source check — blocked sources are Work OS calendars
        source_m = re.search(r"  - Source:\s*(.+)", attrs)
        source = source_m.group(1).strip() if source_m else ""

        # Normalise: strip trailing annotations like "manual (email exchange ...)"
        source_tag = source.split("(")[0].strip()

        if source_tag in _BLOCKED_CALENDAR_SOURCES:
            continue

        # Exclude-by-default: source must be explicitly personal (§16.3)
        if source_tag not in _PERSONAL_CALENDAR_SOURCES:
            continue

        # Extract and validate date
        date_m = re.search(r"  - Date:\s*(.+)", attrs)
        if not date_m:
            continue
        event_date, time_str = _parse_event_date(date_m.group(1).strip())
        if event_date is None:
            continue

        # Filter to window [today, today+days]
        if event_date.toordinal() < today_d.toordinal():
            continue
        if event_date.toordinal() > cutoff_ordinal:
            continue

        results.append({
            "title": title,
            "time": time_str,
            "today": event_date == today_d,
        })

    return results[:days]


# ---------------------------------------------------------------------------
# Work OS signal scan — defense-in-depth (§16.4)
# ---------------------------------------------------------------------------

def _validate_no_work_data(payload: dict) -> list[str]:
    """Return list of potential work OS signal matches found in payload.

    This is a secondary WARNING layer only. The domain allowlist (§16.3) is
    the primary structural guard. This function uses whole-word regex matching
    to reduce false positives (e.g. 'sprint' in a fitness context).

    Returns matched signal list. Empty list means clean.
    Never raises — callers log and continue; export is never aborted on keyword
    matches alone.
    """
    payload_str = json.dumps(payload).lower()
    return [s for s in _WORK_SIGNALS if re.search(s, payload_str)]


# ---------------------------------------------------------------------------
# LAN reachability gate
# ---------------------------------------------------------------------------

def _is_reachable(ha_url: str, timeout: float = 2.0) -> bool:
    """Quick TCP probe to the HA port before attempting a full HTTP request."""
    parsed = urlparse(ha_url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


# ---------------------------------------------------------------------------
# Audit append helper
# ---------------------------------------------------------------------------

def _append_audit(artha_dir: Path, message: str, *, standalone: bool = False) -> None:
    """Append a pipe-table audit entry to state/audit.md (non-blocking).

    Skipped in standalone mode — no pipeline context available.
    Format matches guardrails.py _guardrail_write_audit() convention:
      | <ISO-timestamp> | <event> | <k>:<v> | ...
    """
    if standalone:
        return
    try:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        audit_path = artha_dir / "state" / "audit.md"
        with audit_path.open("a", encoding="utf-8") as fh:
            fh.write(f"| {now_iso} | {message} |\n")
    except Exception:  # noqa: BLE001
        pass  # audit write failure is never blocking


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------

def export_hermes_context(
    artha_dir: Path,
    *,
    standalone: bool = False,
    dry_run: bool = False,
) -> bool:
    """Write Artha context to sensor.artha_context in Home Assistant.

    Returns True on success, False on skip or failure. Never raises.

    Args:
        artha_dir:  Root of the Artha repo (Path).
        standalone: If True, check pipeline sentinel and skip audit log write.
        dry_run:    Build and log payload without POSTing to HA.
    """
    if platform.system() != "Darwin":
        log.debug("Not on macOS — Hermes context export skipped (no LAN access)")
        return False

    # Standalone mode: defer if pipeline is currently writing state files
    if standalone:
        sentinel = artha_dir / "tmp" / ".pipeline_running"
        if sentinel.exists():
            age_s = time.time() - sentinel.stat().st_mtime
            if age_s < 3600:
                log.info(
                    "Pipeline sentinel present (%ds old) — standalone run deferred",
                    int(age_s),
                )
                return False

    # Auth: long-lived HA token from macOS Keychain
    # Convention: get_password(credential_key, service_name) — matches lib/auth.py
    token = keyring.get_password("artha-ha-token", "artha")
    if not token:
        log.warning("artha-ha-token not found in keyring — skipping Hermes context export")
        return False

    # HA URL from connectors.yaml via config_loader (WS-4 Rule 4)
    try:
        cfg = load_config("connectors", _config_dir=str(artha_dir / "config"))
        ha_url = (
            cfg.get("connectors", {})
               .get("homeassistant", {})
               .get("fetch", {})
               .get("ha_url", "")
               .rstrip("/")
        )
    except Exception as exc:
        log.warning("Could not read connectors config: %s — skipping", exc)
        return False

    if not ha_url:
        log.warning("ha_url not found in connectors.yaml — skipping")
        return False

    # LAN reachability gate: 2-second TCP probe (same pattern as homeassistant.py)
    if not _is_reachable(ha_url):
        log.info(
            "HA not reachable at %s — skipping (expected when off home LAN)", ha_url
        )
        return False

    allowed_domains = _load_allowed_domains(artha_dir)

    # Build payload — Personal OS only, no PII per §6.2
    goals_active = _read_goals(artha_dir / "state" / "goals.md", "active")[:3]
    goals_parked = _read_goals(artha_dir / "state" / "goals.md", "parked")[:3]
    p1_items = _read_open_items(
        artha_dir / "state" / "open_items.md", "P1", allowed_domains
    )[:3]
    p2_items = _read_open_items(
        artha_dir / "state" / "open_items.md", "P2", allowed_domains
    )[:5]
    events = _read_upcoming_events(artha_dir, days=7)

    def _fmt(e: dict) -> str:
        return f"{e['title']} {e['time']}".strip()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload: dict = {
        "state": now,
        "attributes": {
            "schema_version": "1.0",
            "friendly_name": "Artha Context",
            "generated": now,
            "goals_active": goals_active,
            "goals_parked": goals_parked,
            "open_items_p1": p1_items,
            "open_items_p2": p2_items,
            "today_events": [_fmt(e) for e in events if e["today"]],
            "week_events": [_fmt(e) for e in events if not e["today"]][:5],  # §6.1 max 5
        },
    }

    # Guard: reject oversized payloads (context window hygiene for Hermes)
    if len(json.dumps(payload)) > 4096:
        log.warning(
            "Hermes context payload exceeds 4096 bytes — truncating week_events"
        )
        payload["attributes"]["week_events"] = payload["attributes"]["week_events"][:3]

    # Work OS signal scan — defense-in-depth (§16.4)
    signals = _validate_no_work_data(payload)
    if signals:
        log.warning(
            "HERMES_WORK_SIGNAL_WARNING: potential work data in payload: %s", signals
        )
        _append_audit(
            artha_dir,
            f"HERMES_WORK_SIGNAL_WARNING | signals:{','.join(signals)[:200]}",
            standalone=standalone,
        )

    if dry_run:
        log.info("[dry-run] Hermes context payload:\n%s", json.dumps(payload, indent=2))
        return True

    # POST to HA with single retry on transient failure
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    endpoint = f"{ha_url}/api/states/sensor.artha_context"
    try:
        r = requests.post(endpoint, headers=headers, json=payload, timeout=5)
        if r.status_code not in (200, 201):
            r = requests.post(endpoint, headers=headers, json=payload, timeout=5)
        ok = r.status_code in (200, 201)
    except requests.RequestException as exc:
        log.warning("Hermes context POST failed: %s", exc)
        return False

    if ok:
        log.info(
            "Hermes context exported to HA sensor.artha_context (HTTP %s)",
            r.status_code,
        )
    else:
        log.warning("Hermes context export returned HTTP %s", r.status_code)
    return ok


# ---------------------------------------------------------------------------
# CLI entry point — standalone / dry-run modes
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Export Artha life context to Home Assistant sensor.artha_context"
    )
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Periodic refresh mode: check pipeline sentinel, skip audit log",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build payload and log it, but do NOT POST to HA",
    )
    args = parser.parse_args()
    ok = export_hermes_context(
        _ARTHA_DIR,
        standalone=args.standalone,
        dry_run=args.dry_run,
    )
    sys.exit(0 if ok else 1)
