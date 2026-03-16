"""
scripts/skills/relationship_pulse.py — Relationship warmth tracker (U-9.1)

Reads circle membership from state/contacts.md YAML frontmatter.
Computes "days since last WhatsApp contact" per person per circle.
Surfaces stale contacts (over cadence threshold) as nudges.

Output is consumed by skill_runner and surfaced to the AI CLI during catch-up.
The AI decides whether to surface the nudge in the briefing.

Skills registry entry (config/skills.yaml):
  relationship_pulse:
    enabled: true
    priority: P1
    cadence: every_run
    requires_vault: false
    description: "Check contact freshness across circles, nudge stale relationships"

Ref: specs/util.md §U-1, §U-9
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .base_skill import BaseSkill

# Cadence → threshold in days
_CADENCE_DAYS: dict[str, int] = {
    "daily_passive": 0,       # No nudge
    "weekly": 7,
    "biweekly": 14,
    "monthly": 30,
    "quarterly": 90,
    "as_needed": 0,           # No nudge
}

_CONTACTS_FILE = "state/contacts.md"


def _load_circles(artha_dir: Path) -> dict[str, Any]:
    """Load circle definitions from contacts.md YAML frontmatter."""
    contacts_path = artha_dir / _CONTACTS_FILE
    if not contacts_path.exists():
        return {}
    content = contacts_path.read_text(encoding="utf-8")
    # Extract YAML frontmatter between --- delimiters
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1))
        return data.get("circles", {}) if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def _load_last_contact_dates(artha_dir: Path) -> dict[str, date]:
    """Extract Last WA contact dates from contacts.md table rows."""
    contacts_path = artha_dir / _CONTACTS_FILE
    if not contacts_path.exists():
        return {}
    content = contacts_path.read_text(encoding="utf-8")
    dates: dict[str, date] = {}
    # Match table rows like: | Name | ... | 2026-03-04 | ...
    for line in content.splitlines():
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 3:
            continue
        name = parts[0].strip()
        if not name or name.startswith("-") or name.lower() in {"name", ""}:
            continue
        # Look for a date pattern in any column
        for part in parts[1:]:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", part)
            if m:
                try:
                    dates[name] = date.fromisoformat(m.group(1))
                    break
                except ValueError:
                    pass
    return dates


class RelationshipPulseSkill(BaseSkill):
    """Check relationship warmth across circles and surface stale contacts."""

    def __init__(self, artha_dir: Path | None = None):
        super().__init__(name="relationship_pulse", priority="P1")
        self.artha_dir = artha_dir or Path(".")

    def pull(self) -> dict[str, Any]:
        """Load circle definitions and last-contact dates from contacts.md."""
        circles = _load_circles(self.artha_dir)
        last_contact = _load_last_contact_dates(self.artha_dir)
        return {"circles": circles, "last_contact": last_contact}

    def parse(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Compute stale contacts per circle."""
        circles: dict[str, Any] = raw_data.get("circles", {})
        last_contact: dict[str, date] = raw_data.get("last_contact", {})
        today = date.today()
        stale_contacts: list[dict[str, Any]] = []
        circle_summaries: list[dict[str, Any]] = []

        for circle_id, circle in circles.items():
            if not isinstance(circle, dict):
                continue
            cadence = circle.get("cadence", "monthly")
            nudge = circle.get("nudge", False)
            if not nudge:
                continue
            threshold_days = _CADENCE_DAYS.get(cadence, 30)
            if threshold_days == 0:
                continue

            members = circle.get("members", [])
            stale_in_circle: list[dict[str, Any]] = []
            for member in members:
                last = last_contact.get(member)
                if last is None:
                    days_since = None
                else:
                    days_since = (today - last).days

                if days_since is None or days_since >= threshold_days:
                    stale_in_circle.append({
                        "name": member,
                        "circle": circle.get("label", circle_id),
                        "days_since_contact": days_since,
                        "cadence_days": threshold_days,
                        "overdue_by": (days_since - threshold_days) if days_since else None,
                        "last_contact": last.isoformat() if last else "never",
                    })

            circle_summaries.append({
                "circle_id": circle_id,
                "label": circle.get("label", circle_id),
                "total_members": len(members),
                "stale_count": len(stale_in_circle),
                "cadence": cadence,
            })
            stale_contacts.extend(sorted(
                stale_in_circle,
                key=lambda x: (x["overdue_by"] is None, -(x["overdue_by"] or 0)),
            ))

        # Most urgent first
        stale_contacts.sort(
            key=lambda x: (x["overdue_by"] is None, -(x["overdue_by"] or 0))
        )

        return {
            "stale_contacts": stale_contacts[:10],  # Top 10 most overdue
            "circle_summaries": circle_summaries,
            "checked_at": today.isoformat(),
            "total_stale": len(stale_contacts),
        }

    def to_dict(self) -> dict[str, Any]:
        result = self.execute()
        if result["status"] != "success":
            return result
        data = result["data"]
        stale = data.get("stale_contacts", [])
        out: list[str] = []
        if not stale:
            out.append("✅ All circles: everyone reached within cadence.")
        else:
            out.append(f"📱 **Relationship Pulse**: {data['total_stale']} contacts overdue")
            for contact in stale[:5]:
                days = contact["days_since_contact"]
                days_str = f"{days}d ago" if days is not None else "never contacted"
                overdue = contact["overdue_by"]
                tag = f" (+{overdue}d overdue)" if overdue else ""
                out.append(
                    f"  · {contact['name']} [{contact['circle']}] — last: {days_str}{tag}"
                )
            if data["total_stale"] > 5:
                out.append(f"  … and {data['total_stale'] - 5} more")

        return {
            "name": self.name,
            "status": result["status"],
            "timestamp": result["timestamp"],
            "summary": "\n".join(out),
            "data": data,
        }

    @property
    def compare_fields(self) -> list:
        return ["total_stale", "checked_at"]
