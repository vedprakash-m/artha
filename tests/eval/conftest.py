"""Shared fixtures for tests/eval/ test suite.

All briefing content is 100% fictional — no real user data (DD-5).
Ref: specs/eval.md EV-12
"""
from __future__ import annotations

import json
import sys
import textwrap
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap — ensure scripts/ is importable (mirrors tests/conftest.py)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Directory scaffolding
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_artha_dir(tmp_path):
    """Minimal Artha directory tree sufficient for eval tests."""
    (tmp_path / "state").mkdir()
    (tmp_path / "tmp").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "briefings").mkdir()
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def rubric(tmp_path):
    """Load rubric.yaml from the eval test directory."""
    rubric_path = Path(__file__).resolve().parent / "rubric.yaml"
    try:
        import yaml  # type: ignore[import]
        return yaml.safe_load(rubric_path.read_text(encoding="utf-8"))
    except Exception:
        # Inline fallback so tests don't fail if PyYAML isn't installed
        return {
            "quality": {"golden_min": 60, "anti_golden_max": 40, "regression_drop_pct": 20},
            "error_budget": {"max_pct": 5.0},
            "corrections": {"per_domain_cap": 10, "global_cap": 50},
            "alerts": {"max_entries": 20, "p1_escalation_hours": 24, "dedup_window_days": 14},
        }


# ---------------------------------------------------------------------------
# Briefing fixtures — all fictional
# ---------------------------------------------------------------------------

_GOLDEN_BRIEFING = textwrap.dedent("""\
    # Catch-Up Briefing — 2026-01-15

    ## 🎯 ONE THING
    Submit Form I-485 by 2026-02-01 (14 days) — schedule attorney review this week.

    ## IMMIGRATION
    - 🔴 URGENT: I-485 priority date current as of Jan 2026 — file within 30 days.
    - Action: Call attorney by Thursday to book filing appointment.
    - Status: Medical exam (Form I-693) completed 2025-12-10, valid for 2 years.

    ## FINANCE
    - 🟠 Mortgage payment $3,200 due 2026-01-20 — auto-pay confirmed, no action needed.
    - 🟡 Annual bonus ($8,500) deposited 2026-01-12 — allocate $3,000 to emergency fund by Jan 25.
    - Review Q4 2025 tax documents received from employer; organize before Feb 15.

    ## HEALTH
    - Annual physical scheduled 2026-02-05 — book bloodwork appointment by Jan 30.
    - Prescription refill: metformin 90-day supply expires 2026-01-28 — call pharmacy.

    ## GOALS
    - Q1 goal "Complete USCIS filing" — 3/5 milestones done, on track.
    - Reading goal 2026: 2/12 books complete. Schedule 30-min session 3×/week.
    """)

_ANTI_GOLDEN_BRIEFING = textwrap.dedent("""\
    # Catch-Up Briefing — 2026-01-15

    ## Today's Update
    There might be some things to look at when you get a chance. Immigration stuff
    may need attention at some unspecified point in time. Finance seems okay more
    or less. Health is probably fine. Goals are going.

    Consider reviewing your situation when convenient. Things may improve.
    """)


@pytest.fixture
def golden_briefing_path(tmp_artha_dir):
    """High-quality fictional briefing — expected to score above rubric golden_min."""
    path = tmp_artha_dir / "briefings" / "2026-01-15.md"
    path.write_text(_GOLDEN_BRIEFING, encoding="utf-8")
    return path


@pytest.fixture
def anti_golden_briefing_path(tmp_artha_dir):
    """Low-quality fictional briefing — expected to score below rubric anti_golden_max."""
    path = tmp_artha_dir / "briefings" / "2026-01-15-anti.md"
    path.write_text(_ANTI_GOLDEN_BRIEFING, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Catch-up run fixtures
# ---------------------------------------------------------------------------

def _make_run(
    i: int,
    quality_score: float = 72.0,
    domains: list[str] | None = None,
) -> dict:
    if domains is None:
        domains = ["immigration", "finance", "health", "goals"]
    return {
        "date": f"2026-01-{i+1:02d}",
        "engagement_rate": round(0.25 + i * 0.01, 3),
        "items_surfaced": 8 + i,
        "correction_count": 1,
        "domains_processed": domains,
        "quality_score": quality_score,
        "compliance_score": 80,
        "model": "claude-3-5-sonnet",
        "schema_version": "1.0.0",
    }


@pytest.fixture
def mock_catch_up_runs():
    """10 fictional catch-up run records with stable quality scores."""
    return [_make_run(i) for i in range(10)]


@pytest.fixture
def mock_regressing_runs():
    """10 fictional runs where the second half quality drops >20%."""
    first_half = [_make_run(i, quality_score=80.0) for i in range(5)]
    second_half = [_make_run(i + 5, quality_score=55.0) for i in range(5)]
    return first_half + second_half


@pytest.fixture
def mock_stale_domain_runs():
    """7 fictional runs — last 3 are missing the 'immigration' domain."""
    early = [_make_run(i, domains=["immigration", "finance", "health"]) for i in range(4)]
    late = [_make_run(i + 4, domains=["finance", "health"]) for i in range(3)]
    return early + late


# ---------------------------------------------------------------------------
# Log fixture helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_log_dir(tmp_path):
    """Empty JSONL log directory for log_digest tests."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    return log_dir


def _write_log_records(log_dir: Path, date_str: str, records: list[dict]) -> None:
    """Write a JSONL log file for the given date."""
    path = log_dir / f"artha.{date_str}.log.jsonl"
    lines = [json.dumps(r) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def log_dir_with_errors(tmp_log_dir):
    """Log dir with one connector exceeding the 20% error rate threshold."""
    _write_log_records(
        tmp_log_dir,
        date.today().isoformat(),  # use today so file is within any lookback window
        [
            {"connector": "graph", "level": "INFO", "ms": 120},
            {"connector": "graph", "level": "ERROR", "ms": 0},
            {"connector": "graph", "level": "ERROR", "ms": 0},
            {"connector": "graph", "level": "INFO", "ms": 110},
            {"connector": "graph", "level": "ERROR", "ms": 0},  # 3/5 = 60%
            {"connector": "outlook", "level": "INFO", "ms": 200},
        ],
    )
    return tmp_log_dir


# ---------------------------------------------------------------------------
# Correction fact fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_facts():
    """A list of correction/threshold facts for filter tests."""
    today = date.today().isoformat()
    future = "2099-12-31"
    past = "2020-01-01"
    return [
        {"type": "correction", "domain": "finance", "value": "Use pre-tax gross income", "ttl": future},
        {"type": "threshold", "domain": "finance", "value": "Alert when savings drop below $5,000", "ttl": future},
        {"type": "correction", "domain": "immigration", "value": "I-94 records via CBP portal", "ttl": future},
        {"type": "correction", "domain": "finance", "value": "Old expired rule", "ttl": past},  # expired
        {"type": "note", "domain": "health", "value": "Doctor appointment notes"},  # wrong type
        {"type": "correction", "domain": "health", "value": "Health correction", "ttl": future},
    ]
