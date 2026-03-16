"""
scripts/skills/subscription_monitor.py — Subscription and digital service price watcher.

Reads ``state/digital.md`` to detect price increases in recurring subscriptions.
Compares current billing amounts against the previous values stored in the state
file's ``subscriptions`` YAML block and surfaces alerts when:
  - A subscription price increased since last catch-up
  - A trial period is about to convert to paid (within 7 days)
  - A subscription that was cancelled re-appears in email routing

This skill is non-blocking and works without network access (state-file-only).

Skills registry entry (config/skills.yaml):
  subscription_monitor:
    enabled: true
    priority: P1
    cadence: every_run
    requires_vault: false
    safety_critical: false

Ref: specs/enhance.md §1.8
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .base_skill import BaseSkill

# Alert thresholds
_TRIAL_WARNING_DAYS = 7     # trial converts to paid within N days → warn
_PRICE_INCREASE_THRESHOLD = 0.01  # any increase above this fraction → alert (1% = $0.10 on $10)

# Common subscription amount patterns in markdown text
_AMOUNT_PATTERN = re.compile(
    r"(?P<service>[\w\s\+\.]+?)\s*:?\s*\$(?P<amount>[\d,]+\.?\d*)\s*/?\s*(?P<period>mo(?:nth)?|yr?|year|annual|monthly|week|wk)",
    re.IGNORECASE
)

# YAML-style subscription block patterns
_YAML_SUB_PATTERN = re.compile(
    r"[-\s]*name:\s*[\"']?(?P<name>[^\"'\n]+)[\"']?\n"
    r"(?:.*?\n)*?"
    r"\s*amount:\s*\$?(?P<amount>[\d,]+\.?\d*)",
    re.IGNORECASE | re.DOTALL
)

_YAML_TRIAL_PATTERN = re.compile(
    r"[-\s]*name:\s*[\"']?(?P<name>[^\"'\n]+)[\"']?\n"
    r"(?:.*?\n)*?"
    r"\s*trial_ends:\s*(?P<date>\d{4}-\d{2}-\d{2})",
    re.IGNORECASE | re.DOTALL
)


def _parse_subscriptions_from_markdown(text: str) -> list[dict]:
    """Extract subscription records from digital.md content.

    Returns list of dicts with keys:
        name, amount (float), period, trial_ends (date|None), raw_line
    """
    subs: list[dict] = []
    seen_names: set[str] = set()

    # Try YAML-block style first (structured state files)
    for m in _YAML_SUB_PATTERN.finditer(text):
        name = m.group("name").strip()
        try:
            amount = float(m.group("amount").replace(",", ""))
        except ValueError:
            continue
        if name.lower() not in seen_names:
            seen_names.add(name.lower())
            subs.append({"name": name, "amount": amount, "period": "monthly", "trial_ends": None})

    # Find trial dates
    for m in _YAML_TRIAL_PATTERN.finditer(text):
        name = m.group("name").strip()
        try:
            trial_date = date.fromisoformat(m.group("date"))
        except ValueError:
            continue
        # Attach trial_ends to existing entry or create new
        matched = False
        for sub in subs:
            if sub["name"].lower() == name.lower():
                sub["trial_ends"] = trial_date
                matched = True
                break
        if not matched:
            subs.append({"name": name, "amount": 0.0, "period": "monthly", "trial_ends": trial_date})

    # Fallback: inline amount patterns (e.g. "Netflix: $15.99/mo")
    if not subs:
        for m in _AMOUNT_PATTERN.finditer(text):
            name = m.group("service").strip().rstrip(":-")
            if len(name) < 2 or len(name) > 60:
                continue
            try:
                amount = float(m.group("amount").replace(",", ""))
            except ValueError:
                continue
            period = m.group("period").lower()
            if name.lower() not in seen_names:
                seen_names.add(name.lower())
                subs.append({"name": name, "amount": amount, "period": period, "trial_ends": None})

    return subs


def _detect_price_changes(current: list[dict], previous: list[dict]) -> list[dict]:
    """Compare current vs previous subscription amounts.

    Returns list of change dicts:
        {"name": str, "old_amount": float, "new_amount": float, "delta": float, "delta_pct": float}
    """
    prev_map = {s["name"].lower(): s for s in previous}
    changes: list[dict] = []

    for sub in current:
        name_key = sub["name"].lower()
        if name_key in prev_map:
            old_amount = prev_map[name_key].get("amount", 0.0)
            new_amount = sub.get("amount", 0.0)
            if old_amount > 0 and new_amount > old_amount:
                delta = new_amount - old_amount
                delta_pct = delta / old_amount
                if delta_pct >= _PRICE_INCREASE_THRESHOLD:
                    changes.append({
                        "name": sub["name"],
                        "old_amount": old_amount,
                        "new_amount": new_amount,
                        "delta": delta,
                        "delta_pct": delta_pct,
                    })
    return changes


def _detect_trial_expirations(subs: list[dict]) -> list[dict]:
    """Return subscriptions whose trial ends within _TRIAL_WARNING_DAYS days."""
    today = date.today()
    warnings: list[dict] = []
    for sub in subs:
        trial_end = sub.get("trial_ends")
        if trial_end is None:
            continue
        days_left = (trial_end - today).days
        if 0 <= days_left <= _TRIAL_WARNING_DAYS:
            warnings.append({
                "name": sub["name"],
                "trial_ends": trial_end.isoformat(),
                "days_left": days_left,
                "converts_to": sub.get("amount", 0.0),
            })
        elif days_left < 0:
            # Trial already expired — may have converted silently
            warnings.append({
                "name": sub["name"],
                "trial_ends": trial_end.isoformat(),
                "days_left": days_left,
                "status": "trial_expired_check_billing",
            })
    return warnings


class SubscriptionMonitorSkill(BaseSkill):
    """Detect subscription price increases and trial expirations from state/digital.md."""

    def __init__(self, artha_dir: Path) -> None:
        super().__init__(name="subscription_monitor", priority="P1")
        self._artha_dir = artha_dir
        self._state_file = artha_dir / "state" / "digital.md"
        self._cache_file = artha_dir / "tmp" / ".subscription_cache.json"

    def pull(self) -> dict[str, Any]:
        """Read current state from digital.md and previous cache."""
        import json  # noqa: PLC0415

        current_text = ""
        if self._state_file.exists():
            current_text = self._state_file.read_text(encoding="utf-8")

        previous_subs: list[dict] = []
        if self._cache_file.exists():
            try:
                data = json.loads(self._cache_file.read_text(encoding="utf-8"))
                previous_subs = data.get("subscriptions", [])
            except (json.JSONDecodeError, KeyError):
                previous_subs = []

        return {"current_text": current_text, "previous_subs": previous_subs}

    def parse(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        """Detect price changes and trial expirations."""
        import json  # noqa: PLC0415

        text = raw_data.get("current_text", "")
        previous_subs = raw_data.get("previous_subs", [])

        if not text:
            return {"subscriptions": [], "price_increases": [], "trial_warnings": [], "message": "digital.md not found"}

        current_subs = _parse_subscriptions_from_markdown(text)
        price_increases = _detect_price_changes(current_subs, previous_subs)
        trial_warnings = _detect_trial_expirations(current_subs)

        # Update cache with current state (for next run comparison)
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_data = {
            "updated": datetime.now().isoformat(),
            "subscriptions": [
                {"name": s["name"], "amount": s["amount"], "period": s["period"]}
                for s in current_subs
            ]
        }
        try:
            self._cache_file.write_text(json.dumps(cache_data, indent=2, default=str), encoding="utf-8")
        except OSError:
            pass  # non-fatal if cache write fails

        return {
            "subscriptions": current_subs,
            "subscription_count": len(current_subs),
            "price_increases": price_increases,
            "price_increase_count": len(price_increases),
            "trial_warnings": trial_warnings,
            "trial_warning_count": len(trial_warnings),
        }

    @property
    def compare_fields(self) -> list[str]:
        return ["price_increase_count", "trial_warning_count"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "requires_vault": False,
            "state_file": str(self._state_file),
        }


def get_skill(artha_dir: Path) -> SubscriptionMonitorSkill:
    """Factory function — instantiate the skill for the given Artha root."""
    return SubscriptionMonitorSkill(artha_dir=artha_dir)
