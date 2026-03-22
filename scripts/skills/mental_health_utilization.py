#!/usr/bin/env python3
# pii-guard: ignore-file — skill outputs are summarized, never raw clinical content
"""
scripts/skills/mental_health_utilization.py — EAP utilization + therapy cadence tracker.

CONNECT Phase 2, §5.1.4 — Mental Health & Behavioral Wellness skill.

Skill contract:
  - Reads state/health.md → ## Behavioral Health → ### EAP / Benefits
  - Parses sessions used vs. limit
  - Alerts: 🟠 if sessions_used / session_limit >= 0.8
  - Alerts: 🟡 if last therapy appointment was >30 days ago and frequency is
    weekly/biweekly (possible care gap)
  - NEVER accesses or interprets clinical content
  - NEVER stores diagnosis, clinical notes, or treatment details

Requirements:
  - Feature flag: connect.domains.mental_health_extension: true
  - State: state/health.md.age must be decryptable (vault required)

Ref: specs/connect.md §5.1.4
"""
from __future__ import annotations

import logging
import re
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

_ARTHA_DIR = Path(__file__).resolve().parent.parent.parent
if str(_ARTHA_DIR) not in sys.path:
    sys.path.insert(0, str(_ARTHA_DIR))

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature flag guard
# ---------------------------------------------------------------------------

def _mental_health_enabled() -> bool:
    try:
        import yaml  # noqa: PLC0415
        cfg_path = _ARTHA_DIR / "config" / "artha_config.yaml"
        if not cfg_path.exists():
            return False
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return bool(cfg.get("connect", {}).get("domains", {}).get("mental_health_extension", False))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# State parsing helpers
# ---------------------------------------------------------------------------

def _parse_eap_section(health_text: str) -> list[dict]:
    """Parse ### EAP / Benefits table from health.md text."""
    records: list[dict] = []
    in_section = False
    in_table = False

    for line in health_text.splitlines():
        if line.strip() == "### EAP / Benefits":
            in_section = True
            in_table = False
            continue
        if in_section and line.startswith("### "):
            # Next subsection — stop
            break
        if in_section and line.startswith("| Benefit"):
            in_table = True
            continue  # header row
        if in_section and in_table and re.match(r"^\|[-| ]+\|", line):
            continue  # separator row
        if in_section and in_table and line.startswith("|"):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 4:
                try:
                    used = int(re.sub(r"[^\d]", "", parts[2]) or "0")
                    limit = int(re.sub(r"[^\d]", "", parts[3]) or "0")
                except ValueError:
                    used = 0
                    limit = 0
                records.append({
                    "benefit": parts[0],
                    "provider": parts[1],
                    "sessions_used": used,
                    "session_limit": limit,
                    "resets": parts[4] if len(parts) > 4 else "",
                })

    return records


def _parse_appointments_section(health_text: str) -> list[dict]:
    """Parse ### Appointments table from ## Behavioral Health section."""
    records: list[dict] = []
    in_bh = False
    in_section = False
    in_table = False

    for line in health_text.splitlines():
        if line.strip() == "## Behavioral Health":
            in_bh = True
            continue
        if in_bh and line.startswith("## ") and line.strip() != "## Behavioral Health":
            break  # left behavioral health section
        if in_bh and line.strip() == "### Appointments":
            in_section = True
            in_table = False
            continue
        if in_bh and in_section and line.startswith("### "):
            break
        if in_bh and in_section and line.startswith("| Person"):
            in_table = True
            continue
        if in_bh and in_section and in_table and re.match(r"^\|[-| ]+\|", line):
            continue
        if in_bh and in_section and in_table and line.startswith("|"):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 4:
                records.append({
                    "person": parts[0],
                    "provider": parts[1],
                    "type": parts[2],
                    "next_appointment": parts[3],
                    "frequency": parts[4] if len(parts) > 4 else "",
                })

    return records


def _days_since(date_str: str) -> int | None:
    """Parse a date string and return days elapsed since then. Returns None if unparseable."""
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(date_str, fmt).date()
            return (date.today() - dt).days
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Main skill entry point
# ---------------------------------------------------------------------------

def run(artha_dir: Path) -> dict[str, Any]:
    """Execute the mental health utilization skill.

    Returns a dict with keys: alerts (list[str]), summary (str).
    Returns early if feature flag is disabled.
    """
    if not _mental_health_enabled():
        return {"alerts": [], "summary": "mental_health_extension: disabled (opt-in feature)"}

    health_state_path = artha_dir / "state" / "health.md"
    if not health_state_path.exists():
        # Vault-encrypted path
        health_state_path = artha_dir / "state" / "health.md.age"
        if not health_state_path.exists():
            return {"alerts": [], "summary": "health state file not found"}

    try:
        health_text = health_state_path.read_text(encoding="utf-8")
    except Exception as exc:
        log.warning("mental_health_utilization: could not read health state: %s", exc)
        return {"alerts": [], "summary": "health state unreadable"}

    alerts: list[str] = []

    # --- EAP utilization check ---
    eap_records = _parse_eap_section(health_text)
    for rec in eap_records:
        if rec["session_limit"] > 0:
            ratio = rec["sessions_used"] / rec["session_limit"]
            if ratio >= 0.8:
                pct = int(ratio * 100)
                alerts.append(
                    f"🟠 EAP ({rec['benefit']}): {rec['sessions_used']}/{rec['session_limit']} sessions used "
                    f"({pct}%) — limit approaching. Resets: {rec['resets'] or 'unknown'}."
                )

    # --- Therapy appointment cadence check ---
    appt_records = _parse_appointments_section(health_text)
    for rec in appt_records:
        freq = rec["frequency"].lower()
        if freq in ("weekly", "biweekly", "bi-weekly", "every 2 weeks"):
            # Check if last appointment was too long ago
            days = _days_since(rec["next_appointment"])
            if days is not None and days > 30:
                alerts.append(
                    f"🟡 Therapy care gap: last appointment for {rec['person']} "
                    f"was {days} days ago (frequency: {rec['frequency']}). "
                    "Consider scheduling a session."
                )

    if not eap_records and not appt_records:
        summary = "Behavioral Health section not yet populated in health state."
    else:
        eap_summary = (
            f"{len(eap_records)} EAP benefit(s) tracked"
            if eap_records else "No EAP records"
        )
        appt_summary = (
            f"{len(appt_records)} behavioral health appointment(s) tracked"
            if appt_records else "No behavioral health appointments"
        )
        summary = f"{eap_summary}. {appt_summary}."

    return {"alerts": alerts, "summary": summary}
