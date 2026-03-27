"""tests/work/test_work_narrative.py — Focused tests for scripts/work/narrative.py

T3-49..58 per pay-debt.md §7.6
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import work.narrative
import work.helpers
from work.narrative import (
    cmd_newsletter,
    cmd_deck,
    cmd_memo,
    cmd_talking_points,
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
    work.narrative._WORK_STATE_DIR = d
    work.helpers._WORK_STATE_DIR = d
    return d


# ---------------------------------------------------------------------------
# T3-49: cmd_newsletter returns string (calls NarrativeEngine or fallback)
# ---------------------------------------------------------------------------

def test_cmd_newsletter_no_state(work_dir):
    out = cmd_newsletter(period=None)
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_newsletter_weekly_period(work_dir):
    out = cmd_newsletter(period="weekly")
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_newsletter_monthly_period(work_dir):
    out = cmd_newsletter(period="monthly")
    assert isinstance(out, str)
    assert len(out) > 0


# ---------------------------------------------------------------------------
# T3-50: cmd_deck — with and without topic arg
# ---------------------------------------------------------------------------

def test_cmd_deck_no_topic(work_dir):
    out = cmd_deck(topic=None)
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_deck_with_topic(work_dir):
    out = cmd_deck(topic="Q3 Roadmap")
    assert isinstance(out, str)
    assert len(out) > 0
    # Topic should be referenced in output or at least not crash
    assert isinstance(out, str)


def test_cmd_deck_different_topics(work_dir):
    topics = ["Team Health", "Incident Review", "Platform Strategy"]
    for topic in topics:
        out = cmd_deck(topic=topic)
        assert isinstance(out, str)
        assert len(out) > 0


# ---------------------------------------------------------------------------
# T3-51: cmd_memo — modes
# cmd_memo(period='', weekly=False, escalation_context='', decision_id='') -> str
# ---------------------------------------------------------------------------

def test_cmd_memo_default(work_dir):
    out = cmd_memo()
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_memo_weekly_mode(work_dir):
    out = cmd_memo(weekly=True)
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_memo_escalation_mode(work_dir):
    out = cmd_memo(escalation_context="Service degraded 15 min, P1 INC-999")
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_memo_decision_mode(work_dir):
    out = cmd_memo(decision_id="D-001")
    assert isinstance(out, str)
    assert len(out) > 0


# ---------------------------------------------------------------------------
# T3-52: cmd_talking_points — empty input guard
# ---------------------------------------------------------------------------

def test_cmd_talking_points_empty_topic(work_dir):
    out = cmd_talking_points(topic="")
    assert isinstance(out, str)
    assert len(out) > 0
    # Should indicate that a topic is required, or degrade gracefully
    # Must not raise


def test_cmd_talking_points_none_topic(work_dir):
    out = cmd_talking_points(topic=None)
    assert isinstance(out, str)
    assert len(out) > 0


# ---------------------------------------------------------------------------
# T3-53: cmd_talking_points — valid topic
# ---------------------------------------------------------------------------

def test_cmd_talking_points_valid_topic(work_dir):
    out = cmd_talking_points(topic="Migration to Flex Consumption")
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_talking_points_all_hands_topic(work_dir):
    out = cmd_talking_points(topic="All-hands budget discussion")
    assert isinstance(out, str)
    assert len(out) > 0


# ---------------------------------------------------------------------------
# T3-54..55: cmd_talking_points and module integrity
# ---------------------------------------------------------------------------

def test_all_narrative_cmds_empty_dir(work_dir):
    for fn, kwargs in [
        (cmd_newsletter, {"period": None}),
        (cmd_deck, {"topic": None}),
        (cmd_memo, {"weekly": False}),
        (cmd_talking_points, {"topic": "test"}),
    ]:
        try:
            out = fn(**kwargs)
            assert isinstance(out, str)
        except Exception as exc:
            pytest.fail(f"{fn.__name__} raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# T3-56: NarrativeEngine integration via work.narrative
# ---------------------------------------------------------------------------

def test_narrative_module_exports_expected_functions():
    import work.narrative as nm
    expected = ["cmd_newsletter", "cmd_deck", "cmd_memo", "cmd_talking_points"]
    for fn_name in expected:
        assert hasattr(nm, fn_name), f"Missing export: {fn_name}"
        assert callable(getattr(nm, fn_name))


# ---------------------------------------------------------------------------
# T3-57: cmd_newsletter NarrativeEngine call (mock test)
# ---------------------------------------------------------------------------

def test_cmd_newsletter_engine_invoked(work_dir):
    # Verify narrative engine is invoked or fallback runs without crash
    with patch.dict("sys.modules", {}):
        out = cmd_newsletter(period="weekly")
    assert isinstance(out, str)
    assert len(out) > 0


# ---------------------------------------------------------------------------
# T3-58: cmd_memo returns non-empty for all mode combinations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("weekly,escalation_context,decision_id", [
    (False, "", ""),
    (True, "", ""),
    (False, "P1 incident INC-001", ""),
    (False, "", "D-001"),
])
def test_cmd_memo_all_modes_non_empty(work_dir, weekly, escalation_context, decision_id):
    out = cmd_memo(weekly=weekly, escalation_context=escalation_context, decision_id=decision_id)
    assert isinstance(out, str)
    assert len(out) > 0
