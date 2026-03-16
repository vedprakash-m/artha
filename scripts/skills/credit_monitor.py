"""
scripts/skills/credit_monitor.py — Credit monitoring alert parser (U-9.4)

Reads state/digital.md (if available) and state/finance.md (if decrypted)
for credit monitoring signals:
  - New hard inquiries
  - Credit score changes
  - New account openings
  - Monitoring alerts from Equifax, TransUnion, CreditKarma, Experian

Since state files may be encrypted, this skill gracefully degrades:
- If digital.md is readable: scan for subscription and account records
- If finance.md is readable (decrypted): scan for credit-related entries
- If neither available: returns empty set (non-blocking)

Skills registry entry (config/skills.yaml):
  credit_monitor:
    enabled: true
    priority: P1
    cadence: daily
    requires_vault: false
    description: "Parse credit monitoring alerts — new inquiries, score changes, new accounts"

Ref: specs/util.md §U-9
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .base_skill import BaseSkill

_DIGITAL_FILE = "state/digital.md"
_FINANCE_FILE = "state/finance.md"

# Known credit monitoring sender patterns (matches routing.yaml)
_CREDIT_SENDERS = frozenset([
    "creditkarma.com", "equifax.com", "experian.com", "transunion.com",
    "chase.com", "wellsfargo.com", "discover.com", "bankofamerica.com",
    "citi.com",
])

# Patterns that indicate credit events deserving attention
_INQUIRY_PATTERN = re.compile(
    r"(hard\s+inquiry|new\s+inquiry|credit\s+pull|credit\s+check)", re.IGNORECASE
)
_SCORE_CHANGE_PATTERN = re.compile(
    r"(credit\s+score|score\s+change|score\s+update|fico|vantage)", re.IGNORECASE
)
_NEW_ACCOUNT_PATTERN = re.compile(
    r"(new\s+account|account\s+opened|credit\s+line\s+increase|new\s+card)", re.IGNORECASE
)
_FRAUD_PATTERN = re.compile(
    r"(fraud\s+alert|suspicious\s+activity|identity\s+theft|unauthorized|breach)", re.IGNORECASE
)


def _scan_state_file(path: Path) -> list[dict[str, Any]]:
    """Scan a state markdown file for credit-related entries."""
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8")
    except (PermissionError, UnicodeDecodeError):
        return []

    alerts: list[dict[str, Any]] = []
    for i, line in enumerate(content.splitlines()):
        line_lower = line.lower()
        alert_type = None
        severity = "🟡"

        if _FRAUD_PATTERN.search(line):
            alert_type = "fraud_alert"
            severity = "🔴"
        elif _INQUIRY_PATTERN.search(line):
            alert_type = "hard_inquiry"
            severity = "🟠"
        elif _NEW_ACCOUNT_PATTERN.search(line):
            alert_type = "new_account"
            severity = "🟠"
        elif _SCORE_CHANGE_PATTERN.search(line):
            alert_type = "score_change"
            severity = "🟡"

        if alert_type:
            excerpt = line.strip()[:120]
            alerts.append({
                "type": alert_type,
                "severity": severity,
                "source": path.name,
                "excerpt": excerpt,
                "line": i + 1,
            })

    return alerts


class CreditMonitorSkill(BaseSkill):
    """Parse credit monitoring signals from state files."""

    def __init__(self, artha_dir: Path | None = None):
        super().__init__(name="credit_monitor", priority="P1")
        self.artha_dir = artha_dir or Path(".")

    def pull(self) -> dict[str, Any]:
        """Scan available state files for credit monitoring signals."""
        alerts: list[dict[str, Any]] = []
        files_scanned: list[str] = []

        for filename in [_DIGITAL_FILE, _FINANCE_FILE]:
            path = self.artha_dir / filename
            if path.exists() and not path.suffix == ".age":
                found = _scan_state_file(path)
                if found or path.exists():
                    files_scanned.append(filename)
                alerts.extend(found)

        return {
            "alerts": alerts,
            "files_scanned": files_scanned,
            "checked_on": date.today().isoformat(),
        }

    def parse(self, raw_data: dict[str, Any]) -> dict[str, Any]:
        alerts = raw_data.get("alerts", [])
        # Deduplicate by excerpt
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for a in alerts:
            key = a["excerpt"][:60]
            if key not in seen:
                seen.add(key)
                unique.append(a)

        # Sort: fraud first, then inquiries, then others
        order = {"fraud_alert": 0, "hard_inquiry": 1, "new_account": 2, "score_change": 3}
        unique.sort(key=lambda x: order.get(x["type"], 99))

        return {
            "alerts": unique,
            "total": len(unique),
            "has_fraud": any(a["type"] == "fraud_alert" for a in unique),
            "files_scanned": raw_data.get("files_scanned", []),
            "checked_on": raw_data.get("checked_on"),
        }

    def to_dict(self) -> dict[str, Any]:
        result = self.execute()
        if result["status"] != "success":
            return result
        data = result["data"]
        out: list[str] = []
        alerts = data.get("alerts", [])

        if data.get("has_fraud"):
            out.append("🚨 **FRAUD ALERT detected in credit monitoring data**")

        if alerts:
            out.append(f"💳 **Credit Monitor** — {data['total']} signal(s) found:")
            for a in alerts[:5]:
                out.append(f"  {a['severity']} [{a['type'].replace('_', ' ').title()}] {a['excerpt'][:80]}")
            if data["total"] > 5:
                out.append(f"  … and {data['total'] - 5} more")
        else:
            if data.get("files_scanned"):
                out.append("✅ Credit Monitor: no alerts found in scanned state files.")
            else:
                out.append("ℹ️ Credit Monitor: state files not available (vault may be encrypted).")

        return {
            "name": self.name,
            "status": result["status"],
            "timestamp": result["timestamp"],
            "summary": "\n".join(out),
            "data": data,
        }

    @property
    def compare_fields(self) -> list:
        return ["total", "has_fraud", "checked_on"]
