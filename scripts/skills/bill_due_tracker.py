"""
scripts/skills/bill_due_tracker.py — Bill due date tracker (U-9.3)

Reads bill due dates and recurring payment entries from:
  - state/occasions.md (Financial & Legal Deadlines section)
  - state/finance.md (if available, decrypted)

Alerts at 7 / 3 / 1 days before due, and flags overdue items.

Skills registry entry (config/skills.yaml):
  bill_due_tracker:
    enabled: true
    priority: P1
    cadence: every_run
    requires_vault: false
    description: "Extract bill due dates, alert 7/3/1 days before, flag overdue"

Ref: specs/util.md §U-9
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .base_skill import BaseSkill

_OCCASIONS_FILE = "state/occasions.md"
_FINANCE_FILE = "state/finance.md"

# Alert thresholds in days
_ALERT_THRESHOLDS = [1, 3, 7]

# Patterns for bill entries extracted from occasions.md
_BILL_REGEX = re.compile(
    r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|",
)

# Date patterns we expect in occasions.md bill tables
_DATE_PATTERNS = [
    re.compile(r"(\w+ \d+, \d{4})"),            # "April 21, 2026"
    re.compile(r"(\d{4}-\d{2}-\d{2})"),          # "2026-04-21"
    re.compile(r"Monthly \((\d+)(?:st|nd|rd|th)?\)"),  # "Monthly (23rd)"
    re.compile(r"Semi-annual \((\w+) & (\w+)\)"),       # "Semi-annual (May & Oct)"
]

_MONTH_MAP = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}


def _next_monthly_due(day_of_month: int, today: date) -> date:
    """Return the next occurrence of day_of_month in the current or next month."""
    candidate = today.replace(day=min(day_of_month, 28))
    if candidate < today:
        if candidate.month == 12:
            candidate = candidate.replace(year=candidate.year + 1, month=1)
        else:
            candidate = candidate.replace(month=candidate.month + 1)
    return candidate


def _parse_bill_date(date_str: str, today: date) -> date | None:
    """Parse a date string from an occasions.md bill table cell."""
    if not date_str:
        return None
    date_str_clean = date_str.strip()

    # ISO format
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", date_str_clean)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # "Month Day, Year"
    m = re.search(r"(\w+) (\d+), (\d{4})", date_str_clean)
    if m:
        mo = _MONTH_MAP.get(m.group(1).lower())
        if mo:
            try:
                return date(int(m.group(3)), mo, int(m.group(2)))
            except ValueError:
                pass

    # "Monthly (NNth)" → next occurrence
    m = re.search(r"[Mm]onthly.*?(\d+)", date_str_clean)
    if m:
        return _next_monthly_due(int(m.group(1)), today)

    # "Semi-annual (May & Oct)"
    m = re.search(r"[Ss]emi-annual.*?(\w+)\s*&\s*(\w+)", date_str_clean)
    if m:
        month1 = _MONTH_MAP.get(m.group(1).lower())
        month2 = _MONTH_MAP.get(m.group(2).lower())
        dates = []
        for mo in filter(None, [month1, month2]):
            try:
                d = today.replace(month=mo, day=1)
                if d < today:
                    d = d.replace(year=d.year + 1)
                dates.append(d)
            except ValueError:
                pass
        if dates:
            return min(dates)

    return None


def _extract_bills_from_occasions(artha_dir: Path, today: date) -> list[dict[str, Any]]:
    """Extract bill rows from the Financial & Legal Deadlines sections of occasions.md."""
    path = artha_dir / _OCCASIONS_FILE
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8")
    bills: list[dict[str, Any]] = []
    in_bills_section = False

    for line in content.splitlines():
        header_lower = line.lstrip("#").strip().lower()
        if line.startswith("##") or line.startswith("###"):
            in_bills_section = any(k in header_lower for k in [
                "financial", "legal", "bill", "deadline", "renewal", "upcoming"
            ])
            continue
        if not in_bills_section:
            continue
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 2:
            continue
        item_name = parts[0].strip()
        if not item_name or item_name.lower() in {"item", "deadline", "---", ""}:
            continue
        if "---" in item_name:
            continue

        # Try second column as date
        date_str = parts[1].strip() if len(parts) > 1 else ""
        notes = parts[2].strip() if len(parts) > 2 else ""

        # Skip rows that are clearly header-like
        if date_str.lower() in {"due date", "date", "target date", ""}:
            continue

        parsed = _parse_bill_date(date_str, today)
        if parsed:
            bills.append({
                "item": item_name,
                "due_date": parsed.isoformat(),
                "notes": notes,
                "source": "occasions.md",
                "is_recurring": any(k in date_str.lower() for k in ["monthly", "annual", "semi"]),
            })

    return bills


class BillDueTrackerSkill(BaseSkill):
    """Track bill due dates and surface alerts 7/3/1 days before."""

    def __init__(self, artha_dir: Path | None = None):
        super().__init__(name="bill_due_tracker", priority="P1")
        self.artha_dir = artha_dir or Path(".")

    def pull(self) -> dict[str, Any]:
        today = date.today()
        bills = _extract_bills_from_occasions(self.artha_dir, today)
        return {"bills": bills, "today": today.isoformat()}

    def parse(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        today = date.fromisoformat(raw_data["today"])
        bills = raw_data.get("bills", [])
        upcoming: list[dict[str, Any]] = []
        overdue: list[dict[str, Any]] = []

        for bill in bills:
            due = date.fromisoformat(bill["due_date"])
            days_until = (due - today).days
            if days_until < 0:
                overdue.append({**bill, "days_overdue": abs(days_until)})
            elif days_until <= 7:
                severity = "🔴" if days_until <= 1 else ("🟠" if days_until <= 3 else "🟡")
                upcoming.append({**bill, "days_until": days_until, "severity": severity})

        upcoming.sort(key=lambda x: x["days_until"])
        overdue.sort(key=lambda x: x["days_overdue"], reverse=True)

        return {
            "upcoming": upcoming,
            "overdue": overdue,
            "total_bills": len(bills),
            "checked_on": today.isoformat(),
        }

    def to_dict(self) -> dict[str, Any]:
        result = self.execute()
        if result["status"] != "success":
            return result
        data = result["data"]
        out: list[str] = []

        if data.get("overdue"):
            out.append(f"🔴 **Overdue bills** ({len(data['overdue'])} items):")
            for b in data["overdue"]:
                out.append(f"  · {b['item']} — {b['days_overdue']}d overdue")

        if data.get("upcoming"):
            out.append(f"📅 **Bills due soon** ({len(data['upcoming'])} items):")
            for b in data["upcoming"]:
                days = b["days_until"]
                day_str = "TODAY" if days == 0 else f"in {days}d ({b['due_date']})"
                out.append(f"  {b['severity']} {b['item']} — due {day_str}")
                if b.get("notes"):
                    out.append(f"     {b['notes']}")
        elif not data.get("overdue"):
            out.append("✅ No bills due in the next 7 days.")

        return {
            "name": self.name,
            "status": result["status"],
            "timestamp": result["timestamp"],
            "summary": "\n".join(out),
            "data": data,
        }

    @property
    def compare_fields(self) -> list:
        return ["total_bills", "checked_on"]


def get_skill(artha_dir=None):
    """Factory called by skill_runner to instantiate this skill."""
    return BillDueTrackerSkill(artha_dir=artha_dir)
