"""tests/eval/test_e2e_llm_smoke.py — End-to-end LLM smoke tests.

Requires ARTHA_SMOKE_MODEL env var to be set (e.g. gpt-4o-mini).
All briefing fixtures are 100% fictional — no real user data (DD-5).
Ref: specs/eval.md EV-12b
"""
from __future__ import annotations

import importlib.util
import os
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("ARTHA_SMOKE_MODEL"),
    reason="ARTHA_SMOKE_MODEL env var not set — skipping LLM smoke tests",
)

# ---------------------------------------------------------------------------
# Module loading helpers
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def eval_scorer():
    return _load_module("eval_scorer", _SCRIPTS_DIR / "eval_scorer.py")


# ---------------------------------------------------------------------------
# Fictional minimal briefing fixture
# ---------------------------------------------------------------------------
_SMOKE_BRIEFING = textwrap.dedent("""\
    # Catch-Up Briefing — 2026-04-01

    ## 🎯 ONE THING
    Schedule dental appointment by 2026-04-10 — 6-month checkup overdue.

    ## HEALTH
    - 🟡 Dental: last cleaning was 2025-09-15 (6+ months ago) — book by Apr 10.
    - Annual physical: completed 2026-02-05, all results normal.

    ## FINANCE
    - 🟢 Mortgage auto-pay confirmed for 2026-04-15 ($2,400).
    - Review Q1 spending summary: $1,200 dining (budget: $900) — 33% over.

    ## GOALS
    - Learning goal: Complete Python certification — 3 modules remaining.
    - Fitness: 4/5 workouts completed this week.
""")


@pytest.fixture
def smoke_briefing_path(tmp_path):
    """Write smoke briefing to a temp file and return its path."""
    p = tmp_path / "smoke_briefing.md"
    p.write_text(_SMOKE_BRIEFING, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Smoke tests (guarded by pytestmark — skip when no LLM available)
# ---------------------------------------------------------------------------


def test_e2e_smoke_produces_briefing(smoke_briefing_path, eval_scorer):
    """EV-12b S1: Scoring a well-formed briefing returns a result dict."""
    result = eval_scorer.score_briefing(smoke_briefing_path)

    assert isinstance(result, dict), "score_briefing() must return a dict"
    assert "quality_score" in result, "Result must include quality_score"
    assert "dimensions" in result, "Result must include dimensions"
    assert result["briefing_file"] == str(smoke_briefing_path)


def test_e2e_smoke_quality_floor(smoke_briefing_path, eval_scorer):
    """EV-12b S2: Well-formed briefing must score at or above minimum floor (30)."""
    result = eval_scorer.score_briefing(smoke_briefing_path)

    assert result["quality_score"] is not None, "quality_score must not be None"
    assert result["quality_score"] >= 30, (
        f"Smoke briefing scored {result['quality_score']:.1f}, expected >= 30. "
        f"Dimensions: {result['dimensions']}"
    )


def test_e2e_smoke_compliance_not_null(smoke_briefing_path, eval_scorer):
    """EV-12b S3: score_briefing() always returns a non-null compliance_score."""
    result = eval_scorer.score_briefing(smoke_briefing_path)

    assert result.get("compliance_score") is not None, (
        "compliance_score must not be None — even if scorer falls back to 0.0"
    )
