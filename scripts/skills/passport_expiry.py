"""
scripts/skills/passport_expiry.py — Passport expiry tracker skill.

Reads passport expiry dates from the decrypted immigration state file
(``state/immigration.md.age`` → ``state/immigration.md``) and computes
days-to-expiry for each passport on file.

Raises alerts:
  - 🔴 CRITICAL: expires within 60 days (many countries require 3-6 months)
  - 🟠 URGENT: expires within 90 days
  - 🟡 STANDARD: expires within 180 days (6 months — safe travel window closing)
  - No alert: >180 days remaining

``requires_vault: true`` — this skill cannot run unless the vault is decrypted.
It exits gracefully (non-blocking) when the decrypted state file is absent.

Skills registry entry (config/skills.yaml):
  passport_expiry:
    enabled: true
    priority: P1
    cadence: every_run
    requires_vault: true
    safety_critical: false

Ref: specs/enhance.md §1.7
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .base_skill import BaseSkill

# Days-to-expiry thresholds
_CRITICAL_DAYS = 60    # <60 days: many countries deny entry
_URGENT_DAYS = 90      # <90 days: avoid international booking
_STANDARD_DAYS = 180   # <180 days: safe travel window closing

# Regex patterns to find passport expiry in immigration.md markdown content
_EXPIRY_PATTERNS = [
    # "passport_expiry: 2027-01-15" (YAML front-matter or inline YAML)
    re.compile(r"passport_expiry:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE),
    # "Expires: January 15, 2027" or "expiry: Jan 15, 2027"
    re.compile(r"(?:expir(?:y|es|ation)[:\s]+)(\w+ \d{1,2},?\s*\d{4})", re.IGNORECASE),
    # "valid through: 01/15/2027"
    re.compile(r"valid\s+through[:\s]+(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE),
    # "expires 2027-01-15"
    re.compile(r"\bexpires?\s+(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE),
]

# Patterns for passport holder name (to label alerts)
_HOLDER_PATTERNS = [
    # "passport_holder: John Doe" or "holder: Primary User"
    re.compile(r"passport_holder:\s*(.+)", re.IGNORECASE),
    re.compile(r"holder:\s*(.+)", re.IGNORECASE),
]


def _parse_date(date_str: str) -> date | None:
    """Try multiple date formats. Returns None if unparseable."""
    formats = [
        "%Y-%m-%d",             # ISO: 2027-01-15
        "%B %d, %Y",            # "January 15, 2027"
        "%B %d %Y",             # "January 15 2027"
        "%b %d, %Y",            # "Jan 15, 2027"
        "%b %d %Y",             # "Jan 15 2027"
        "%m/%d/%Y",             # 01/15/2027
        "%d/%m/%Y",             # 15/01/2027 (international format)
    ]
    date_str = date_str.strip().rstrip(".")
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def _extract_passports(text: str) -> list[dict]:
    """Parse immigration.md text and extract passport expiry records.

    Returns list of dicts:
        {"holder": str, "expiry_date": date, "days_remaining": int, "alert_level": str}
    """
    entries: list[dict] = []

    # Split content into blocks — look for YAML-like passport blocks
    # Pattern: look for "passport:" sections potentially wrapped by "---"
    # Also handle free-form markdown with inline expiry mentions

    # Try extracting holder separately to label results
    # Find all expiry dates first
    found_expiries: list[date] = []
    for pattern in _EXPIRY_PATTERNS:
        for m in pattern.finditer(text):
            d = _parse_date(m.group(1))
            if d is not None and d not in found_expiries:
                found_expiries.append(d)

    # Try to find holders to label them
    found_holders: list[str] = []
    for pattern in _HOLDER_PATTERNS:
        for m in pattern.finditer(text):
            name = m.group(1).strip()
            if name and name not in found_holders:
                found_holders.append(name)

    today = date.today()
    for i, expiry_date in enumerate(found_expiries):
        holder = found_holders[i] if i < len(found_holders) else ("Primary" if i == 0 else f"Person {i+1}")
        days_remaining = (expiry_date - today).days

        if days_remaining <= _CRITICAL_DAYS:
            alert_level = "critical"
        elif days_remaining <= _URGENT_DAYS:
            alert_level = "urgent"
        elif days_remaining <= _STANDARD_DAYS:
            alert_level = "standard"
        else:
            alert_level = "ok"

        entries.append({
            "holder": holder,
            "expiry_date": expiry_date.isoformat(),
            "days_remaining": days_remaining,
            "alert_level": alert_level,
        })

    return entries


class PassportExpirySkill(BaseSkill):
    """Check passport expiry dates from decrypted immigration state."""

    def __init__(self, artha_dir: Path) -> None:
        super().__init__(name="passport_expiry", priority="P1")
        self._artha_dir = artha_dir
        self._state_file = artha_dir / "state" / "immigration.md"

    def pull(self) -> str:
        """Read the decrypted immigration state file."""
        if not self._state_file.exists():
            # State file not present — either vault locked or domain not bootstrapped
            return ""
        return self._state_file.read_text(encoding="utf-8")

    def parse(self, raw_data: str) -> dict[str, Any]:
        """Extract passport expiry records from the state file content."""
        if not raw_data:
            return {"passports": [], "vault_required": True, "message": "immigration.md not found (vault may be locked)"}

        passports = _extract_passports(raw_data)
        expiring_soon = [p for p in passports if p["alert_level"] != "ok"]
        any_critical = any(p["alert_level"] == "critical" for p in passports)
        any_urgent = any(p["alert_level"] == "urgent" for p in passports)

        return {
            "passports": passports,
            "expiring_soon_count": len(expiring_soon),
            "highest_alert": "critical" if any_critical else ("urgent" if any_urgent else ("standard" if expiring_soon else "ok")),
        }

    @property
    def compare_fields(self) -> list[str]:
        # Alert if any passport's alert_level changes or days_remaining crosses a threshold
        return ["expiring_soon_count", "highest_alert"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "requires_vault": True,
            "state_file": str(self._state_file),
        }


def get_skill(artha_dir: Path) -> PassportExpirySkill:
    """Factory function — instantiate the skill for the given Artha root."""
    return PassportExpirySkill(artha_dir=artha_dir)
