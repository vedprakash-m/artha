#!/usr/bin/env python3
"""
status_view.py — Script-backed /status command renderer
========================================================
Reads state/health-check.md (plaintext, no vault required).
Displays last catch-up stats, connector health, and system integrity
from the structured YAML blocks maintained by the catch-up workflow.

Usage:
  python scripts/status_view.py
  python scripts/status_view.py --format flash
  python scripts/status_view.py --format standard   (default)
  python scripts/status_view.py --format digest

Output: Markdown formatted status report.

Exit codes:
  0 — success
  1 — health-check.md missing or unreadable

Ref: specs/enhance.md §1.13 / §10.0.1
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent
_STATE_DIR = _ARTHA_DIR / "state"
_HEALTH_FILE = _STATE_DIR / "health-check.md"


def _read_health_check() -> str:
    if not _HEALTH_FILE.exists():
        return ""
    try:
        return _HEALTH_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


def _extract_last_catch_up(content: str) -> dict:
    """Extract the last_catch_up block and key metrics from the structured YAML."""
    result: dict = {}
    # Extract last_catch_up timestamp from the "Last Catch-Up" block
    m = re.search(r"last_catch_up:\s*['\"]?([^'\"\n]+)['\"]?", content)
    if m:
        result["last_catch_up"] = m.group(1).strip()
    for key in (
        "emails_processed", "alerts_generated", "open_items_added", "open_items_closed",
        "todo_sync", "preflight", "duration_seconds", "context_window_pct",
        "context_pressure", "briefing_format", "volume_tier", "domains_active",
        "session_mode",
    ):
        m = re.search(rf"{key}:\s*([^\n#]+)", content)
        if m:
            result[key] = m.group(1).strip().strip("'\"")
    return result


def _extract_rolling_accuracy(content: str) -> dict:
    """Extract rolling accuracy pulse from health-check.md."""
    result: dict = {}
    m = re.search(r"rolling_7d[^\n]*\n((?:  [^\n]+\n)*)", content)
    if m:
        block = m.group(1)
        for kv in re.finditer(r"(\w+):\s*([^\n]+)", block):
            result[kv.group(1)] = kv.group(2).strip()
    return result


def _extract_connector_health(content: str) -> list[dict]:
    """Extract connector health entries from health-check.md if present."""
    entries = []
    section_m = re.search(r"## Connector Health\n```yaml\n(.*?)```", content, re.DOTALL)
    if not section_m:
        return entries
    block = section_m.group(1)
    for m in re.finditer(
        r"-\s+name:\s+(\S+)[^\n]*\n(?:.*?status:\s+(\S+)[^\n]*\n)?(?:.*?last_success:\s+([^\n]+)\n)?",
        block, re.DOTALL
    ):
        entries.append({
            "name": m.group(1),
            "status": (m.group(2) or "unknown").strip(),
            "last_success": (m.group(3) or "").strip(),
        })
    return entries


def _extract_run_history(content: str, max_runs: int = 5) -> list[dict]:
    """Extract the last N catch-up runs from the run history block."""
    runs = []
    history_m = re.search(r"catch_up_runs:\n(.*?)(?:\n##|\Z)", content, re.DOTALL)
    if not history_m:
        return runs
    block = history_m.group(1)
    current: dict = {}
    for line in block.split("\n"):
        stripped = line.strip()
        if stripped.startswith("- timestamp:"):
            if current:
                runs.append(current)
                if len(runs) >= max_runs:
                    break
            current = {"timestamp": stripped.split(":", 1)[1].strip().strip("'\"")}
        elif current and ":" in stripped and not stripped.startswith("#"):
            k, _, v = stripped.partition(":")
            current[k.strip()] = v.strip().strip("'\"")
    if current and len(runs) < max_runs:
        runs.append(current)
    return runs


def _status_icon(value: str, key: str = "") -> str:
    """Return a status icon for a given value."""
    val = value.lower()
    if key in ("context_pressure", "preflight"):
        return {"green": "✅", "pass": "✅", "yellow": "🟡", "warn": "🟡",
                "red": "🔴", "fail": "🔴", "critical": "🔴"}.get(val, "⬜")
    if key == "todo_sync":
        return {"ok": "✅", "skipped": "⬜", "failed": "🔴"}.get(val, "⬜")
    return ""


def _format_flash(stats: dict, runs: list[dict]) -> str:
    last = stats.get("last_catch_up", "never")
    pressure = stats.get("context_pressure", "?")
    preflight = stats.get("preflight", "?")
    alerts = stats.get("alerts_generated", "?")
    mode = stats.get("session_mode", "")
    mode_str = f" · mode: {mode}" if mode else ""
    lines = [
        "## Artha Status — Flash",
        f"Last catch-up: **{last}**{mode_str}",
        f"Alerts: {alerts} · Pressure: {_status_icon(pressure, 'context_pressure')} {pressure} · Preflight: {_status_icon(preflight, 'preflight')} {preflight}",
        f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
    ]
    return "\n".join(lines)


def _format_standard(stats: dict, connectors: list[dict], runs: list[dict]) -> str:
    lines = ["## Artha Status"]

    # Last catch-up summary
    lines.append("\n### Last Catch-Up")
    last = stats.get("last_catch_up", "never")
    lines.append(f"- **Timestamp:** {last}")
    if "session_mode" in stats:
        lines.append(f"- **Mode:** {stats['session_mode']}")
    if "briefing_format" in stats:
        lines.append(f"- **Format:** {stats['briefing_format']}")
    for k, label in (
        ("emails_processed", "Emails processed"),
        ("alerts_generated", "Alerts generated"),
        ("open_items_added", "Open items added"),
        ("open_items_closed", "Open items closed"),
        ("domains_active", "Domains active"),
        ("duration_seconds", "Duration (s)"),
        ("context_window_pct", "Context window %"),
    ):
        if k in stats:
            lines.append(f"- **{label}:** {stats[k]}")
    for k, label in (("preflight", "Preflight"), ("todo_sync", "Todo sync"), ("context_pressure", "Context pressure")):
        if k in stats:
            icon = _status_icon(stats[k], k)
            lines.append(f"- **{label}:** {icon} {stats[k]}")

    # Connector health
    if connectors:
        lines.append("\n### Connector Health")
        for c in connectors:
            icon = "✅" if c["status"] == "ok" else ("🔴" if c["status"] == "error" else "⬜")
            last_s = f" · last success: {c['last_success']}" if c.get("last_success") else ""
            lines.append(f"- {icon} **{c['name']}** ({c['status']}){last_s}")

    # Recent runs table
    if runs:
        lines.append("\n### Recent Runs (last 5)")
        lines.append("| Timestamp | Emails | Alerts | OI+ | Pressure | Mode |")
        lines.append("|-----------|--------|--------|-----|----------|------|")
        for r in runs:
            ts = r.get("timestamp", "?")[:16]
            emails = r.get("emails_processed", "?")
            alerts = r.get("alerts_generated", "?")
            oi = r.get("open_items_added", "?")
            pres = r.get("context_pressure", "?")
            mode = r.get("session_mode", "normal")
            lines.append(f"| {ts} | {emails} | {alerts} | {oi} | {pres} | {mode} |")

    lines.append(f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def _format_digest(content: str, stats: dict, connectors: list[dict], runs: list[dict]) -> str:
    """Digest: full connector health + full run history + system integrity section."""
    standard = _format_standard(stats, connectors, runs)
    lines = [standard, "\n---\n"]

    # Domain hit rates if present
    hit_rate_m = re.search(r"domain_hit_rates:\n(.*?)(?:\n##|\Z)", content, re.DOTALL)
    if hit_rate_m:
        lines.append("### Domain Hit Rates")
        lines.append("```yaml")
        lines.append(hit_rate_m.group(1).rstrip())
        lines.append("```")

    # System integrity section
    integrity_m = re.search(r"## System Integrity\n(.*?)(?:\n##|\Z)", content, re.DOTALL)
    if integrity_m:
        lines.append("\n### System Integrity")
        lines.append(integrity_m.group(1).strip())

    lines.append(f"\n_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Artha Status Viewer")
    parser.add_argument(
        "--format", choices=["flash", "standard", "digest"], default="standard",
        help="Output density (default: standard)"
    )
    args = parser.parse_args()

    content = _read_health_check()
    if not content:
        print(
            "⚠ state/health-check.md not found or empty. Run a catch-up first to populate.",
            file=sys.stderr,
        )
        return 1

    stats = _extract_last_catch_up(content)
    connectors = _extract_connector_health(content)
    runs = _extract_run_history(content, max_runs=5)

    if args.format == "flash":
        print(_format_flash(stats, runs))
    elif args.format == "digest":
        print(_format_digest(content, stats, connectors, runs))
    else:
        print(_format_standard(stats, connectors, runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
