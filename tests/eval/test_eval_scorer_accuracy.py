"""tests/eval/test_eval_scorer_accuracy.py — Accuracy / golden-file tests for eval_scorer.

Validates that score_briefing() is idempotent and produces the expected
quality scores for a high-quality vs. low-quality briefing.
Ref: specs/eval.md EV-12, T-EV-12-04, T-EV-12-05, T-EV-12-06
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
_ARTHA_DIR = Path(__file__).resolve().parent.parent.parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def scorer():
    return _load_module("eval_scorer_accuracy", _SCRIPTS_DIR / "eval_scorer.py")


# ===========================================================================
# T-EV-12-04: Idempotency — scoring the same file twice yields the same score
# ===========================================================================

def test_identical_briefing_same_score(scorer, tmp_path):
    """T-EV-12-04: score_briefing() must be idempotent for the same input."""
    brief = tmp_path / "brief.md"
    brief.write_text(
        "# Daily Briefing\n"
        "- Pay $500 tax by 2026-02-15\n"
        "- Finance: portfolio up +12% YTD\n"
    )
    r1 = scorer.score_briefing(str(brief), artha_dir=str(_ARTHA_DIR))
    r2 = scorer.score_briefing(str(brief), artha_dir=str(_ARTHA_DIR))
    assert r1["quality_score"] == r2["quality_score"], (
        f"score_briefing() returned different scores on identical input: "
        f"{r1['quality_score']} vs {r2['quality_score']}"
    )


# ===========================================================================
# T-EV-12-05: High-quality briefing → quality_score >= 70
# ===========================================================================

def test_high_quality_briefing_scores_high(scorer, tmp_path):
    """T-EV-12-05: A content-rich briefing with actions must score >= 70."""
    brief = tmp_path / "hq.md"
    brief.write_text(
        "# Artha Catch-Up Briefing\n\n"
        "## Finance\n"
        "- Q1 estimated tax payment of $2,400 due 2026-04-15 — confirmed\n"
        "- Portfolio rebalance: Move 5% from bonds to VTSAX by end of March\n"
        "- Action: Transfer $500 to emergency fund by 2026-03-28\n\n"
        "## Kids\n"
        "- School open enrollment opens 2026-02-20 at portal.school.edu\n"
        "- Action: Submit medical clearance forms by 2026-03-01\n\n"
        "## Health\n"
        "- Annual physical scheduled for 2026-03-15 at 10am\n"
        "- Action: Complete lab work by 2026-03-10\n"
    )
    result = scorer.score_briefing(str(brief), artha_dir=str(_ARTHA_DIR))
    assert result["quality_score"] >= 70, (
        f"Expected quality_score >= 70 for high-quality briefing, "
        f"got {result['quality_score']}"
    )


# ===========================================================================
# T-EV-12-06: Low-quality (vague) briefing → quality_score <= 40
# ===========================================================================

def test_low_quality_briefing_scores_low(scorer, tmp_path):
    """T-EV-12-06: A vague, content-free briefing must score <= 40."""
    brief = tmp_path / "lq.md"
    brief.write_text(
        "# Update\n"
        "Things happened. Check stuff. Everything is okay. No actions needed.\n"
        "Generally fine. Nothing urgent. All domains nominal.\n"
    )
    result = scorer.score_briefing(str(brief), artha_dir=str(_ARTHA_DIR))
    assert result["quality_score"] <= 40, (
        f"Expected quality_score <= 40 for vague briefing, "
        f"got {result['quality_score']}"
    )
