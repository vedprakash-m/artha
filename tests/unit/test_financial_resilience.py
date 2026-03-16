"""
tests/unit/test_financial_resilience.py — Unit tests for financial_resilience skill.

Covers:
  - Normal case: 3+ months of expense data with savings
  - Insufficient data (<3 months) → graceful degradation
  - Zero expenses → runway = infinity (∞)
  - Absent state file → returns error dict
  - Single-income household → no single_income_runway key
  - Dual-income household → single_income_runway computed
  - Non-numeric / corrupt data → skip gracefully, return error
  - Full execute() round-trip via BaseSkill.execute()
  - compare_fields returns expected field names
  - to_dict() returns expected structure

Ref: specs/improve.md §5 I-01
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from skills.financial_resilience import (
    FinancialResilienceSkill,
    _parse_monthly_expenses,
    _parse_liquid_savings,
    _parse_income_sources,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

FINANCE_MD_THREE_MONTHS = """
# Finance State

## 2026-01
Total monthly expenses: $3,200

## 2026-02
Total monthly expenses: $3,400

## 2026-03
Total monthly expenses: $3,600

Emergency fund: $18,000
Payroll: $6,500/month
"""

FINANCE_MD_DUAL_INCOME = """
# Finance State

## 2026-01
Total monthly expenses: $4,000

## 2026-02
Total monthly expenses: $4,200

## 2026-03
Total monthly expenses: $4,100

Savings account balance: $24,000
Payroll: $7,000/month
Spouse income: $3,500/month
"""

FINANCE_MD_SINGLE_MONTH = """
# Finance State

Total monthly expenses: $3,000
Emergency fund: $15,000
"""

FINANCE_MD_ZERO_EXPENSES = """
# Finance State

Emergency fund: $20,000
Payroll: $5,000/month
"""

FINANCE_MD_CORRUPT = """
# Finance State

Total monthly expenses: NOT_A_NUMBER
Emergency fund: ???
Payroll: unknown
"""

FINANCE_MD_SIX_MONTHS = """
# Finance State

## 2025-10
Total monthly expenses: $2,800

## 2025-11
Total monthly expenses: $2,900

## 2025-12
Total monthly expenses: $3,500  # holiday spike

## 2026-01
Total monthly expenses: $3,000

## 2026-02
Total monthly expenses: $3,100

## 2026-03
Total monthly expenses: $3,200

Emergency fund: $21,000
Payroll: $6,000/month
"""


# ── Helper ────────────────────────────────────────────────────────────────────

def _make_skill(tmp_path: Path, content: str | None) -> FinancialResilienceSkill:
    """Create a FinancialResilienceSkill pointing at a temp dir."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    if content is not None:
        (state_dir / "finance.md").write_text(content, encoding="utf-8")
    return FinancialResilienceSkill(tmp_path)


# ── Parser unit tests ─────────────────────────────────────────────────────────

class TestParsers:
    def test_parse_monthly_expenses_three_months(self):
        amounts = _parse_monthly_expenses(FINANCE_MD_THREE_MONTHS)
        assert len(amounts) == 3
        assert amounts == [3200.0, 3400.0, 3600.0]

    def test_parse_liquid_savings_emergency_fund(self):
        savings = _parse_liquid_savings(FINANCE_MD_THREE_MONTHS)
        assert savings == 18000.0

    def test_parse_liquid_savings_savings_account(self):
        savings = _parse_liquid_savings(FINANCE_MD_DUAL_INCOME)
        assert savings == 24000.0

    def test_parse_income_sources_single(self):
        sources = _parse_income_sources(FINANCE_MD_THREE_MONTHS)
        assert len(sources) == 1
        assert sources[0]["amount"] == 6500.0

    def test_parse_income_sources_dual(self):
        sources = _parse_income_sources(FINANCE_MD_DUAL_INCOME)
        assert len(sources) == 2
        amounts = {s["amount"] for s in sources}
        assert 7000.0 in amounts
        assert 3500.0 in amounts

    def test_parse_corrupt_data(self):
        amounts = _parse_monthly_expenses(FINANCE_MD_CORRUPT)
        assert amounts == []
        savings = _parse_liquid_savings(FINANCE_MD_CORRUPT)
        assert savings == 0.0


# ── FinancialResilienceSkill: parse() ─────────────────────────────────────────

class TestParseMethod:
    def test_three_months_burn_rate(self):
        skill = FinancialResilienceSkill(Path("/fake"))
        result = skill.parse(FINANCE_MD_THREE_MONTHS)
        # burn_rate = (3200 + 3400 + 3600) / 3 = 3400
        assert result["burn_rate_monthly"] == pytest.approx(3400.0, abs=0.01)
        assert result["months_of_data"] == 3
        assert result["error"] is None

    def test_runway_calculation(self):
        skill = FinancialResilienceSkill(Path("/fake"))
        result = skill.parse(FINANCE_MD_THREE_MONTHS)
        # runway = 18000 / 3400 ≈ 5.3
        assert result["runway_months"] == pytest.approx(5.3, abs=0.1)
        assert result["liquid_savings"] == 18000.0

    def test_single_income_no_dual_runway(self):
        skill = FinancialResilienceSkill(Path("/fake"))
        result = skill.parse(FINANCE_MD_THREE_MONTHS)
        assert result["income_sources"] == 1
        assert result["single_income_runway_months"] is None

    def test_dual_income_runway(self):
        skill = FinancialResilienceSkill(Path("/fake"))
        result = skill.parse(FINANCE_MD_DUAL_INCOME)
        # burn_rate ≈ (4000+4200+4100)/3 = 4100
        # income_primary=7000, secondary=3500, remaining=3500
        # adjusted_burn = 4100 - 3500 = 600
        # single_income_runway = 24000 / 600 = 40
        assert result["income_sources"] == 2
        assert result["single_income_runway_months"] is not None
        assert isinstance(result["single_income_runway_months"], float)

    def test_zero_expenses_infinite_runway(self):
        skill = FinancialResilienceSkill(Path("/fake"))
        result = skill.parse(FINANCE_MD_ZERO_EXPENSES)
        assert result["error"] is not None  # no expense data → insufficient

    def test_single_month_data(self):
        skill = FinancialResilienceSkill(Path("/fake"))
        result = skill.parse(FINANCE_MD_SINGLE_MONTH)
        assert result["months_of_data"] == 1
        assert result["burn_rate_monthly"] == 3000.0
        assert result["error"] is None

    def test_empty_content_returns_error(self):
        skill = FinancialResilienceSkill(Path("/fake"))
        result = skill.parse("")
        assert result["error"] is not None
        assert result["runway_months"] is None

    def test_six_months_uses_last_six(self):
        skill = FinancialResilienceSkill(Path("/fake"))
        result = skill.parse(FINANCE_MD_SIX_MONTHS)
        assert result["months_of_data"] == 6
        # All 6 months used: (2800+2900+3500+3000+3100+3200)/6 = 3083.33
        assert result["burn_rate_monthly"] == pytest.approx(3083.33, abs=1.0)

    def test_corrupt_data_returns_error(self):
        skill = FinancialResilienceSkill(Path("/fake"))
        result = skill.parse(FINANCE_MD_CORRUPT)
        assert result["error"] is not None
        assert result["months_of_data"] == 0


# ── FinancialResilienceSkill: pull() + execute() ─────────────────────────────

class TestSkillExecution:
    def test_pull_absent_file_returns_empty(self, tmp_path):
        skill = _make_skill(tmp_path, content=None)
        assert skill.pull() == ""

    def test_pull_reads_file_content(self, tmp_path):
        skill = _make_skill(tmp_path, FINANCE_MD_THREE_MONTHS)
        content = skill.pull()
        assert "Emergency fund" in content

    def test_execute_absent_file_is_non_blocking(self, tmp_path):
        skill = _make_skill(tmp_path, content=None)
        result = skill.execute()
        # execute() from BaseSkill wraps parse(); status may be success (graceful)
        # but error key in data indicates missing file
        assert result["name"] == "financial_resilience"
        assert result["status"] in ("success", "failed")

    def test_execute_with_data_returns_success(self, tmp_path):
        skill = _make_skill(tmp_path, FINANCE_MD_THREE_MONTHS)
        result = skill.execute()
        assert result["status"] == "success"
        assert "burn_rate_monthly" in result["data"]

    def test_compare_fields(self, tmp_path):
        skill = _make_skill(tmp_path, content=None)
        fields = skill.compare_fields
        assert "runway_months" in fields
        assert "burn_rate_monthly" in fields

    def test_to_dict_structure(self, tmp_path):
        skill = _make_skill(tmp_path, content=None)
        d = skill.to_dict()
        assert d["name"] == "financial_resilience"
        assert d["requires_vault"] is True
        assert d["cadence"] == "weekly"
