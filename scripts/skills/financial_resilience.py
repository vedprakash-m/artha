"""
scripts/skills/financial_resilience.py — Financial resilience calculator.

Reads ``state/finance.md`` (decrypted) and computes:
  - Monthly burn rate (average of last 3–6 months of expenses)
  - Emergency fund runway = liquid_savings ÷ monthly_burn_rate
  - Single-income scenario runway (if dual-income household detected)
  - Discretionary vs non-discretionary split (when data available)

This converts static financial records into actionable intelligence without
offering financial advice — it surfaces facts only.

Output is a dict consumed by skill_runner and surfaced to the AI CLI during
catch-up. The AI decides whether to surface the data in the briefing.

Skills registry entry (config/skills.yaml):
  financial_resilience:
    enabled: true
    priority: P1
    cadence: weekly
    requires_vault: true
    safety_critical: false
    description: "Compute burn rate, runway, and resilience metrics from finance state."

**Boundary:** This skill computes numbers. It does NOT interpret them as "good"
or "bad" and does NOT recommend any financial action. The briefing format in
prompts/finance.md governs how the AI surfaces these facts.

Ref: specs/improve.md §5 I-01
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from .base_skill import BaseSkill

# ── Regex patterns ────────────────────────────────────────────────────────────

# Match a monthly expense total line, e.g.:
#   "Total expenses: $3,421.50"  or  "monthly_expenses: 3421.50"
_MONTHLY_TOTAL_PATTERNS = [
    re.compile(
        r"(?:total\s+(?:monthly\s+)?expenses?|monthly_expenses?)[:\s]+\$?([\d,]+\.?\d*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\|\s*(?:Total|Expenses?)\s*\|\s*\$?([\d,]+\.?\d*)\s*\|",
        re.IGNORECASE,
    ),
]

# Match a savings/liquid balance line, e.g.:
#   "Emergency fund: $18,500"  or  "liquid_savings: 18500"
#   "Savings account: $12,000"
_SAVINGS_PATTERNS = [
    re.compile(
        r"(?:emergency[_\s]fund|liquid[_\s]savings?)[:\s]+\$?([\d,]+\.?\d*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:savings?\s+(?:account\s+)?balance|checking\s+balance)[:\s]+\$?([\d,]+\.?\d*)",
        re.IGNORECASE,
    ),
]

# Match income source lines, e.g.:
#   "Payroll: $6,500"  or  "income_primary: 6500"  or  "Salary: $6,500/month"
_INCOME_PATTERNS = [
    re.compile(
        r"(?:payroll|salary|income_primary|take[_\-]home|net[_\s]income|direct[_\s]deposit)[:\s]+\$?([\d,]+\.?\d*)\s*(?:/\s*mo(?:nth)?)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:income_secondary|spouse[_\s]income|partner[_\s]income)[:\s]+\$?([\d,]+\.?\d*)\s*(?:/\s*mo(?:nth)?)?",
        re.IGNORECASE,
    ),
]

# Month header patterns for multi-month tracking sections, e.g.:
#   "## January 2026"  or  "### 2026-01"
_MONTH_HEADER = re.compile(
    r"(?:##+ (?:\w+ \d{4}|\d{4}-\d{2}))"
    r"|(?:month[:\s]+(?:\d{4}-\d{2}|\w+ \d{4}))",
    re.IGNORECASE,
)


def _parse_amount(text: str) -> float | None:
    """Convert a string like '3,421.50' or '3421' to float, else None."""
    try:
        return float(text.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _extract_first_match(patterns: list, text: str) -> float | None:
    """Return the first non-None float match from a list of patterns."""
    for pattern in patterns:
        for m in pattern.finditer(text):
            val = _parse_amount(m.group(1))
            if val is not None and val > 0:
                return val
    return None


def _parse_monthly_expenses(text: str) -> list[float]:
    """
    Extract a list of monthly expense totals from the finance state file.

    Strategy:
    1. If finance.md has a multi-month table, extract each month's total.
    2. If only a single total is present, return it as a one-element list.
    3. Fall back to budget_categories totals if explicit totals are absent.
    """
    amounts: list[float] = []

    # Pass 1: look for per-month total lines in chronological section blocks
    months = list(_MONTH_HEADER.finditer(text))
    if months:
        # Split text by month header and extract totals from each section
        for i, m in enumerate(months):
            start = m.end()
            end = months[i + 1].start() if i + 1 < len(months) else len(text)
            section = text[start:end]
            val = _extract_first_match(_MONTHLY_TOTAL_PATTERNS, section)
            if val is not None:
                amounts.append(val)

    # Pass 2: if no per-month breakdown, grab the single current total
    if not amounts:
        val = _extract_first_match(_MONTHLY_TOTAL_PATTERNS, text)
        if val is not None:
            amounts.append(val)

    return amounts


def _parse_liquid_savings(text: str) -> float:
    """Extract liquid / emergency savings from the finance state file."""
    val = _extract_first_match(_SAVINGS_PATTERNS, text)
    return val if val is not None else 0.0


def _parse_income_sources(text: str) -> list[dict]:
    """
    Extract income source entries.

    Returns a list of dicts: [{"source": str, "amount": float}, ...]
    Identifies primary vs secondary income to enable single-income stress scenario.
    """
    sources: list[dict] = []
    labels = ["primary", "secondary"]

    for idx, pattern in enumerate(_INCOME_PATTERNS):
        for m in pattern.finditer(text):
            val = _parse_amount(m.group(1))
            if val is not None and val > 0:
                label = labels[min(idx, len(labels) - 1)]
                # Avoid duplicates
                if not any(s["amount"] == val for s in sources):
                    sources.append({"source": label, "amount": val})

    return sources


class FinancialResilienceSkill(BaseSkill):
    """Compute financial resilience metrics from decrypted finance state."""

    def __init__(self, artha_dir: Path) -> None:
        super().__init__(name="financial_resilience", priority="P1")
        self._artha_dir = artha_dir
        # Support both encrypted (runtime) and plain (vault-decrypted) state files
        self._state_file = artha_dir / "state" / "finance.md"

    def pull(self) -> str:
        """Read the (decrypted) finance state file. Returns empty string if absent."""
        if not self._state_file.exists():
            return ""
        try:
            return self._state_file.read_text(encoding="utf-8")
        except OSError:
            return ""

    def parse(self, raw_data: str) -> dict[str, Any]:
        """
        Extract resilience metrics from finance state content.

        Returns a standardised dict:
        {
          "burn_rate_monthly": float,       — avg monthly expenses (last ≤6 months)
          "months_of_data": int,            — how many months fed the burn rate
          "liquid_savings": float,
          "runway_months": float | "∞",
          "single_income_runway_months": float | "∞" | None,  — None if single income
          "income_sources": int,            — count of detected income streams
          "error": str | None,              — set if data insufficient
        }
        """
        if not raw_data:
            return {
                "burn_rate_monthly": 0.0,
                "months_of_data": 0,
                "liquid_savings": 0.0,
                "runway_months": None,
                "single_income_runway_months": None,
                "income_sources": 0,
                "error": "finance.md not found — vault may be locked or domain not bootstrapped",
            }

        # ── Parse components ────────────────────────────────────────────────
        expenses = _parse_monthly_expenses(raw_data)
        savings = _parse_liquid_savings(raw_data)
        income_sources = _parse_income_sources(raw_data)

        if not expenses:
            return {
                "burn_rate_monthly": 0.0,
                "months_of_data": 0,
                "liquid_savings": round(savings, 2),
                "runway_months": None,
                "single_income_runway_months": None,
                "income_sources": len(income_sources),
                "error": "insufficient_data — no monthly expense totals found in finance.md",
            }

        # Use up to the last 6 months
        window = expenses[-6:]
        burn_rate = sum(window) / len(window)

        if burn_rate <= 0:
            runway: float | str = "∞"
        else:
            runway = round(savings / burn_rate, 1) if savings > 0 else 0.0

        # Single-income stress scenario
        single_income_runway = None
        if len(income_sources) > 1:
            primary = max(income_sources, key=lambda s: s["amount"])
            remaining_income = sum(s["amount"] for s in income_sources) - primary["amount"]
            if remaining_income >= burn_rate:
                # Secondary income alone covers burn → still infinite runway
                single_income_runway = "∞"
            elif remaining_income > 0:
                # Remaining income partially offsets burn
                adjusted_burn = burn_rate - remaining_income
                single_income_runway = round(savings / adjusted_burn, 1) if savings > 0 else 0.0
            else:
                # Secondary income is 0 — full expenditure from savings
                single_income_runway = runway

        return {
            "burn_rate_monthly": round(burn_rate, 2),
            "months_of_data": len(window),
            "liquid_savings": round(savings, 2),
            "runway_months": runway,
            "single_income_runway_months": single_income_runway,
            "income_sources": len(income_sources),
            "error": None,
        }

    @property
    def compare_fields(self) -> list[str]:
        """Alert when runway or burn rate changes materially."""
        return ["runway_months", "burn_rate_monthly", "single_income_runway_months"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "requires_vault": True,
            "cadence": "weekly",
            "state_file": str(self._state_file),
        }


def get_skill(artha_dir: Path) -> FinancialResilienceSkill:
    """Factory function called by skill_runner.py."""
    return FinancialResilienceSkill(artha_dir)
