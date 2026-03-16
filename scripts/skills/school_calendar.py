"""
scripts/skills/school_calendar.py — School calendar tracker (U-9.5)

Reads school events from:
  1. state/occasions.md (school milestones, parent-teacher conference dates)
  2. state/kids.md (if available — Canvas LMS grades and assignments)
  3. state/calendar.md (school-related calendar events)

Surfaces:
  - Upcoming school events within 3 days (🔴), 7 days (🟠), 14 days (🟡)
  - No-school days within the next 7 days
  - Parth and Trisha specific milestones
  - Canvas grade alerts (if kids.md is populated)

Skills registry entry (config/skills.yaml):
  school_calendar:
    enabled: true
    priority: P1
    cadence: daily
    requires_vault: false
    description: "Track LWSD school events, breaks, PTC dates; alert 3 days ahead"

Ref: specs/util.md §U-9, §U-3
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .base_skill import BaseSkill

_OCCASIONS_FILE = "state/occasions.md"
_KIDS_FILE = "state/kids.md"
_CALENDAR_FILE = "state/calendar.md"

_ALERT_WINDOWS = {
    "red": 3,
    "orange": 7,
    "yellow": 14,
}

# Keywords that indicate school-related events in calendar entries
_SCHOOL_KEYWORDS = re.compile(
    r"(ptc|parent.teacher|lwsd|tesla stem|inglewood|school|grade|assignment|"
    r"no school|early release|half day|spring break|winter break|summer break|"
    r"report card|progress report|graduation|orientation|back.to.school)",
    re.IGNORECASE,
)

# Grade alert patterns in kids.md
_GRADE_PATTERN = re.compile(
    r"(failing|incomplete|missing|overdue|grade:\s*[DFdf]|gpa.*\b[01]\.[0-9])", re.IGNORECASE
)


def _parse_iso_date(s: str) -> date | None:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _scan_calendar_for_school_events(artha_dir: Path, today: date, window_days: int) -> list[dict[str, Any]]:
    """Scan state/calendar.md for school-related events in the next window_days."""
    path = artha_dir / _CALENDAR_FILE
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8")
    except (PermissionError, UnicodeDecodeError):
        return []

    events: list[dict[str, Any]] = []
    for line in content.splitlines():
        if not _SCHOOL_KEYWORDS.search(line):
            continue
        d = _parse_iso_date(line)
        if d is None:
            continue
        days_until = (d - today).days
        if 0 <= days_until <= window_days:
            severity = "🔴" if days_until <= 3 else ("🟠" if days_until <= 7 else "🟡")
            events.append({
                "event": line.strip()[:120],
                "date": d.isoformat(),
                "days_until": days_until,
                "severity": severity,
                "source": "calendar.md",
            })
    return events


def _scan_kids_for_grade_alerts(artha_dir: Path) -> list[dict[str, Any]]:
    """Scan state/kids.md for grade or assignment alerts."""
    path = artha_dir / _KIDS_FILE
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8")
    except (PermissionError, UnicodeDecodeError):
        return []

    alerts: list[dict[str, Any]] = []
    for line in content.splitlines():
        if _GRADE_PATTERN.search(line):
            alerts.append({
                "type": "grade_alert",
                "severity": "🟠",
                "excerpt": line.strip()[:100],
                "source": "kids.md",
            })
    return alerts


def _extract_school_events_from_occasions(artha_dir: Path, today: date, window_days: int) -> list[dict[str, Any]]:
    """Extract school-relevant milestones from occasions.md."""
    path = artha_dir / _OCCASIONS_FILE
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8")
    events: list[dict[str, Any]] = []
    in_school = False

    for line in content.splitlines():
        header_lower = line.lstrip("#").strip().lower()
        if line.startswith("#"):
            in_school = any(k in header_lower for k in ["school", "education", "kids"])
        if not in_school and not _SCHOOL_KEYWORDS.search(line):
            continue
        d = _parse_iso_date(line)
        if d is None:
            continue
        days_until = (d - today).days
        if 0 <= days_until <= window_days:
            severity = "🔴" if days_until <= 3 else ("🟠" if days_until <= 7 else "🟡")
            events.append({
                "event": line.strip()[:120],
                "date": d.isoformat(),
                "days_until": days_until,
                "severity": severity,
                "source": "occasions.md",
            })
    return events


class SchoolCalendarSkill(BaseSkill):
    """Track school calendar events and surface upcoming dates for both kids."""

    def __init__(self, artha_dir: Path | None = None):
        super().__init__(name="school_calendar", priority="P1")
        self.artha_dir = artha_dir or Path(".")

    def pull(self) -> dict[str, Any]:
        today = date.today()
        events = (
            _scan_calendar_for_school_events(self.artha_dir, today, 14)
            + _extract_school_events_from_occasions(self.artha_dir, today, 14)
        )
        grade_alerts = _scan_kids_for_grade_alerts(self.artha_dir)

        # Deduplicate events by (date, event snippet)
        seen: set[tuple[str, str]] = set()
        unique_events: list[dict[str, Any]] = []
        for e in events:
            key = (e["date"], e["event"][:40])
            if key not in seen:
                seen.add(key)
                unique_events.append(e)

        return {
            "events": sorted(unique_events, key=lambda x: x["days_until"]),
            "grade_alerts": grade_alerts,
            "checked_on": today.isoformat(),
        }

    def parse(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        events = raw_data.get("events", [])
        grade_alerts = raw_data.get("grade_alerts", [])
        return {
            "upcoming_events": events[:10],
            "grade_alerts": grade_alerts[:5],
            "total_events": len(events),
            "total_grade_alerts": len(grade_alerts),
            "checked_on": raw_data.get("checked_on"),
        }

    def to_dict(self) -> dict[str, Any]:
        result = self.execute()
        if result["status"] != "success":
            return result
        data = result["data"]
        out: list[str] = []

        if data.get("grade_alerts"):
            out.append(f"📚 **Grade Alerts** ({data['total_grade_alerts']} items):")
            for a in data["grade_alerts"]:
                out.append(f"  {a['severity']} {a['excerpt']}")

        if data.get("upcoming_events"):
            out.append(f"🏫 **School Calendar** ({data['total_events']} event(s) ahead):")
            for e in data["upcoming_events"][:5]:
                days = e["days_until"]
                day_str = "TODAY" if days == 0 else f"in {days}d"
                out.append(f"  {e['severity']} {day_str}: {e['event'][:80]}")
        elif not data.get("grade_alerts"):
            out.append("✅ School Calendar: no upcoming events in the next 14 days.")

        return {
            "name": self.name,
            "status": result["status"],
            "timestamp": result["timestamp"],
            "summary": "\n".join(out),
            "data": data,
        }

    @property
    def compare_fields(self) -> list:
        return ["total_events", "checked_on"]


def get_skill(artha_dir=None):
    """Factory called by skill_runner to instantiate this skill."""
    return SchoolCalendarSkill(artha_dir=artha_dir)
