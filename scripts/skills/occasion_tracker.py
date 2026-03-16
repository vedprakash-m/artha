"""
scripts/skills/occasion_tracker.py — Upcoming occasions tracker (U-9.2)

Reads state/occasions.md and surfaces:
  - Birthdays within 7 days (🔴) / 14 days (🟠) / 30 days (🟡)
  - Anniversaries within 30 days
  - Cultural/religious festivals within 7 days
  - US public holidays within 7 days

Generates message suggestions for WhatsApp greetings.
Cross-references with contacts.md circle membership for reach-out context.

Skills registry entry (config/skills.yaml):
  occasion_tracker:
    enabled: true
    priority: P1
    cadence: every_run
    requires_vault: false
    description: "7-day lookahead for birthdays, festivals, anniversaries; greeting suggestions"

Ref: specs/util.md §U-2, §U-9
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from .base_skill import BaseSkill

_OCCASIONS_FILE = "state/occasions.md"

# Alert windows
_BIRTHDAY_RED_DAYS = 3
_BIRTHDAY_ORANGE_DAYS = 7
_BIRTHDAY_YELLOW_DAYS = 14
_FESTIVAL_WINDOW_DAYS = 7
_HOLIDAY_WINDOW_DAYS = 3

# Language map based on relationship/origin
_HINDI_RELATIONS = {"sister", "brother", "bhai", "didi"}


def _parse_date_flexible(date_str: str) -> date | None:
    """Parse ISO-8601 (YYYY-MM-DD) dates. Returns None on failure."""
    if not date_str:
        return None
    date_str = date_str.strip()
    # ISO format YYYY-MM-DD
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _this_year_occurrence(birthday: date, today: date) -> date:
    """Given a DOB, return this year's birthday (or next year if already passed)."""
    try:
        this_year = birthday.replace(year=today.year)
    except ValueError:
        # Feb 29 in non-leap year
        this_year = date(today.year, 2, 28)
    if this_year < today:
        try:
            return birthday.replace(year=today.year + 1)
        except ValueError:
            return date(today.year + 1, 2, 28)
    return this_year


def _load_occasions(artha_dir: Path) -> dict[str, Any]:
    """Parse occasions.md to extract birthday, festival, and anniversary rows."""
    path = artha_dir / _OCCASIONS_FILE
    if not path.exists():
        return {"birthdays": [], "festivals": [], "anniversaries": [], "holidays": []}

    content = path.read_text(encoding="utf-8")
    birthdays: list[dict[str, Any]] = []
    festivals: list[dict[str, Any]] = []
    anniversaries: list[dict[str, Any]] = []
    holidays: list[dict[str, Any]] = []

    current_section = ""
    for line in content.splitlines():
        # Track section headers
        if line.startswith("## "):
            current_section = line.lstrip("#").strip().lower()
        elif line.startswith("### "):
            current_section = line.lstrip("#").strip().lower()

        if not line.startswith("|") or line.startswith("| ---") or line.startswith("|---"):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if not parts or parts[0].lower() in {"person", "festival", "holiday", "event", "name", ""}:
            continue

        # Birthday rows: | Person | DOB/Date | Age | ... |
        if "birthday" in current_section and len(parts) >= 2:
            name = parts[0]
            dob = _parse_date_flexible(parts[1])
            if dob and name:
                relationship = parts[3] if len(parts) > 3 else ""
                phone = parts[4] if len(parts) > 4 else ""
                birthdays.append({
                    "name": name,
                    "dob": dob.isoformat(),
                    "relationship": relationship,
                    "phone": phone.strip(),
                })

        # Festival rows: | Festival | Date | Significance | Action |
        elif any(k in current_section for k in ["festival", "cultural", "religious"]) and len(parts) >= 2:
            name = parts[0]
            d = _parse_date_flexible(parts[1])
            if d and name and d.year >= date.today().year:
                action = parts[3].strip() if len(parts) > 3 else ""
                festivals.append({"name": name, "date": d.isoformat(), "action": action})

        # Holiday rows: | Holiday | Date | Notes |
        elif "us public" in current_section or "holiday" in current_section:
            name = parts[0]
            d = _parse_date_flexible(parts[1])
            if d and name and d.year >= date.today().year:
                holidays.append({"name": name, "date": d.isoformat()})

        # Anniversary rows: | Event | Date | Milestone |
        elif "anniversary" in current_section or "wedding" in current_section:
            name = parts[0]
            # Anniversary is a recurring date — parse the month/day from "April 29, 2007"
            full_date_m = re.search(r"(\w+ \d+, \d{4})", parts[1]) if len(parts) > 1 else None
            if full_date_m:
                try:
                    from datetime import datetime as _dt
                    d = _dt.strptime(full_date_m.group(1), "%B %d, %Y").date()
                    milestone = parts[2].strip() if len(parts) > 2 else ""
                    anniversaries.append({
                        "name": name,
                        "original_date": d.isoformat(),
                        "milestone": milestone,
                    })
                except ValueError:
                    pass

    return {
        "birthdays": birthdays,
        "festivals": festivals,
        "anniversaries": anniversaries,
        "holidays": holidays,
    }


def _greeting_suggestion(name: str, event_type: str, days_until: int) -> str:
    """Generate a brief, warm greeting suggestion."""
    first_name = name.split()[0] if name else name
    urgency = "today" if days_until == 0 else f"in {days_until} day{'s' if days_until > 1 else ''}"
    if event_type == "birthday":
        return f"Happy Birthday {first_name}! 🎂 Wishing you a wonderful year ahead."
    elif event_type == "diwali":
        return "Happy Diwali! 🪔 May the festival of lights bring joy and prosperity."
    elif event_type == "holi":
        return "Happy Holi! 🌈 Wishing you a colourful and joyful celebration."
    elif event_type == "festival":
        return f"Warm wishes for {name}! 🙏"
    elif event_type == "anniversary":
        return f"Happy Anniversary! 💐 Wishing you many more beautiful years together."
    return f"Happy {event_type.title()}, {first_name}!"


class OccasionTrackerSkill(BaseSkill):
    """Surface upcoming occasions and generate greeting suggestions."""

    def __init__(self, artha_dir: Path | None = None):
        super().__init__(name="occasion_tracker", priority="P1")
        self.artha_dir = artha_dir or Path(".")

    def pull(self) -> dict[str, Any]:
        return _load_occasions(self.artha_dir)

    def parse(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        today = date.today()
        upcoming: list[dict[str, Any]] = []

        # Process birthdays
        for entry in raw_data.get("birthdays", []):
            dob = _parse_date_flexible(entry.get("dob", ""))
            if not dob:
                continue
            next_birthday = _this_year_occurrence(dob, today)
            days_until = (next_birthday - today).days
            if days_until < 0 or days_until > 30:
                continue
            severity = "🔴" if days_until <= _BIRTHDAY_RED_DAYS else (
                "🟠" if days_until <= _BIRTHDAY_ORANGE_DAYS else "🟡"
            )
            age = today.year - dob.year if days_until <= _BIRTHDAY_RED_DAYS else (today.year + 1 - dob.year if next_birthday.year > today.year else today.year - dob.year)
            upcoming.append({
                "type": "birthday",
                "name": entry["name"],
                "relationship": entry.get("relationship", ""),
                "date": next_birthday.isoformat(),
                "days_until": days_until,
                "severity": severity,
                "age_turning": age,
                "phone": entry.get("phone", ""),
                "suggestion": _greeting_suggestion(entry["name"], "birthday", days_until),
            })

        # Process festivals
        for entry in raw_data.get("festivals", []):
            d = _parse_date_flexible(entry.get("date", ""))
            if not d:
                continue
            days_until = (d - today).days
            if days_until < 0 or days_until > _FESTIVAL_WINDOW_DAYS:
                continue
            upcoming.append({
                "type": "festival",
                "name": entry["name"],
                "date": d.isoformat(),
                "days_until": days_until,
                "severity": "🟠" if days_until <= 2 else "🟡",
                "action": entry.get("action", ""),
                "suggestion": _greeting_suggestion(
                    entry["name"],
                    entry["name"].lower().replace(" ", "_"),
                    days_until,
                ),
            })

        # Process anniversaries
        for entry in raw_data.get("anniversaries", []):
            d = _parse_date_flexible(entry.get("original_date", ""))
            if not d:
                continue
            this_year_ann = _this_year_occurrence(d, today)
            days_until = (this_year_ann - today).days
            if days_until < 0 or days_until > 30:
                continue
            upcoming.append({
                "type": "anniversary",
                "name": entry["name"],
                "date": this_year_ann.isoformat(),
                "days_until": days_until,
                "severity": "🟠" if days_until <= 3 else "🟡",
                "milestone": entry.get("milestone", ""),
                "suggestion": _greeting_suggestion(entry["name"], "anniversary", days_until),
            })

        # Process US public holidays (informational only, 3-day window)
        for entry in raw_data.get("holidays", []):
            d = _parse_date_flexible(entry.get("date", ""))
            if not d:
                continue
            days_until = (d - today).days
            if days_until < 0 or days_until > _HOLIDAY_WINDOW_DAYS:
                continue
            upcoming.append({
                "type": "holiday",
                "name": entry["name"],
                "date": d.isoformat(),
                "days_until": days_until,
                "severity": "🔵",
            })

        # Sort by days_until ascending
        upcoming.sort(key=lambda x: x["days_until"])
        return {
            "upcoming": upcoming,
            "total": len(upcoming),
            "checked_on": today.isoformat(),
        }

    def to_dict(self) -> dict[str, Any]:
        result = self.execute()
        if result["status"] != "success":
            return result
        data = result["data"]
        upcoming = data.get("upcoming", [])
        out: list[str] = []
        if not upcoming:
            out.append("✅ No upcoming occasions in the next 30 days.")
        else:
            out.append(f"🗓️ **Occasions ahead** ({data['total']} items):")
            for item in upcoming:
                days = item["days_until"]
                day_str = "TODAY" if days == 0 else f"in {days}d"
                name = item["name"]
                sev = item.get("severity", "🟡")
                suggestion = item.get("suggestion", "")
                if item["type"] == "birthday":
                    age = item.get("age_turning", "")
                    age_str = f" — turning {age}" if age else ""
                    out.append(f"  {sev} **{name}** birthday {day_str}{age_str}")
                    if suggestion and days <= 7:
                        out.append(f'     💬 Suggest: "{suggestion}"')
                elif item["type"] == "festival":
                    action = item.get("action", "")
                    out.append(f"  {sev} **{name}** {day_str}" + (f" — {action}" if action else ""))
                elif item["type"] == "anniversary":
                    out.append(f"  {sev} **{name}** anniversary {day_str}")
                elif item["type"] == "holiday":
                    out.append(f"  {sev} {name} {day_str} (US holiday)")
        return {
            "name": self.name,
            "status": result["status"],
            "timestamp": result["timestamp"],
            "summary": "\n".join(out),
            "data": data,
        }

    @property
    def compare_fields(self) -> list:
        return ["total", "checked_on"]


def get_skill(artha_dir=None):
    """Factory called by skill_runner to instantiate this skill."""
    return OccasionTrackerSkill(artha_dir=artha_dir)
