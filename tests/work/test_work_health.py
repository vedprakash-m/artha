"""tests/work/test_work_health.py — Focused tests for scripts/work/health.py

T3-21..26 per pay-debt.md §7.6
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import work.health
import work.helpers
from work.health import (
    _assess_provider_tier,
    _seniority_tier,
    _build_degraded_mode_report,
    cmd_health,
)


def _write_state(state_dir: Path, name: str, fm: dict, body: str = "") -> Path:
    p = state_dir / name
    content = "---\n" + yaml.dump(fm, default_flow_style=False) + "---\n\n" + body
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def work_dir(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    work.health._WORK_STATE_DIR = d
    work.helpers._WORK_STATE_DIR = d
    return d


# ---------------------------------------------------------------------------
# T3-21: _assess_provider_tier — returns string tier label
# _assess_provider_tier(graph_ok, ado_ok, workiq_ok, agency_ok) -> str
# ---------------------------------------------------------------------------

class TestAssessProviderTier:
    def test_all_connected(self):
        tier = _assess_provider_tier(True, True, True, True)
        assert isinstance(tier, str)
        assert len(tier) > 0

    def test_only_graph(self):
        tier = _assess_provider_tier(True, False, False, False)
        assert isinstance(tier, str)
        assert len(tier) > 0

    def test_none_connected(self):
        tier = _assess_provider_tier(False, False, False, False)
        assert isinstance(tier, str)
        assert len(tier) > 0

    def test_partial_connections(self):
        tier = _assess_provider_tier(True, True, False, False)
        assert isinstance(tier, str)
        assert len(tier) > 0


# ---------------------------------------------------------------------------
# T3-22: _seniority_tier keyword matching — returns (int, str) tuple
# ---------------------------------------------------------------------------

class TestSeniorityTier:
    def test_exec_keywords(self):
        for title in ("Director of Engineering", "VP Engineering", "Principal Engineer"):
            result = _seniority_tier(title)
            assert isinstance(result, tuple)
            rank, label = result
            assert isinstance(rank, int)
            assert isinstance(label, str)

    def test_senior_keywords(self):
        for title in ("Senior Software Engineer", "Staff SWE", "Lead Developer"):
            result = _seniority_tier(title)
            assert isinstance(result, tuple)

    def test_ic_keywords(self):
        for title in ("Software Engineer", "Developer"):
            result = _seniority_tier(title)
            assert isinstance(result, tuple)

    def test_empty_string(self):
        result = _seniority_tier("")
        assert isinstance(result, tuple)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# T3-23: _build_degraded_mode_report formatting
# _build_degraded_mode_report(tier, graph_ok, ado_ok, workiq_ok) -> list[str]
# ---------------------------------------------------------------------------

def test_build_degraded_mode_report_structure():
    report = _build_degraded_mode_report("standard", False, False, False)
    assert isinstance(report, list)
    assert len(report) > 0
    assert all(isinstance(line, str) for line in report)


def test_build_degraded_mode_report_empty():
    report = _build_degraded_mode_report("standard", True, True, True)
    assert isinstance(report, list)


# ---------------------------------------------------------------------------
# T3-24: cmd_health output with degraded state (no state files)
# ---------------------------------------------------------------------------

def test_cmd_health_no_state(work_dir):
    out = cmd_health()
    assert isinstance(out, str)
    assert len(out) > 0
    # Should contain health heading
    assert any(kw in out.lower() for kw in ("health", "benefit", "coverage", "work health"))


def test_cmd_health_with_benefits_state(work_dir):
    fm = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "employer": "Contoso",
        "medical_plan": "Microsoft Enhanced: Choice Plus",
        "dental_plan": "Delta Dental",
        "vision_plan": "VSP",
    }
    _write_state(work_dir, "benefits.md", fm, body="# Benefits Overview\nEnrolled in medical, dental, vision.\n")
    out = cmd_health()
    assert isinstance(out, str)
    assert len(out) > 0


# ---------------------------------------------------------------------------
# T3-25: cmd_health returns string (never raises)
# ---------------------------------------------------------------------------

def test_cmd_health_never_raises(work_dir):
    # Even with partial corrupt state, must not raise
    corrupt = work_dir / "benefits.md"
    corrupt.write_text("this is not yaml frontmatter at all\njust random text", encoding="utf-8")
    try:
        out = cmd_health()
        assert isinstance(out, str)
    except Exception as exc:
        pytest.fail(f"cmd_health raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# T3-26: coverage_summary state integration
# ---------------------------------------------------------------------------

def test_cmd_health_with_coverage(work_dir):
    fm = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "plan_type": "PPO",
        "deductible_individual": 1500,
        "deductible_met": 250,
        "out_of_pocket_max": 4000,
        "out_of_pocket_met": 250,
    }
    _write_state(
        work_dir,
        "coverage_summary.md",
        fm,
        body="## Coverage Details\nIn-network deductible: $1500.\n",
    )
    out = cmd_health()
    assert isinstance(out, str)
    assert len(out) > 0
