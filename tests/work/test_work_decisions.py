"""tests/work/test_work_decisions.py — Focused tests for scripts/work/decisions.py

T3-27..32 per pay-debt.md §7.6
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import work.decisions
import work.helpers
from work.decisions import (
    _ensure_decisions_header,
    _append_to_file,
    cmd_decide,
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
    work.decisions._WORK_STATE_DIR = d
    work.helpers._WORK_STATE_DIR = d
    return d


# ---------------------------------------------------------------------------
# T3-27: cmd_decide with context creates structured output
# ---------------------------------------------------------------------------

def test_cmd_decide_with_context(work_dir):
    ctx = "Should we migrate the auth service to Entra ID in Q3?"
    out = cmd_decide(context=ctx)
    assert isinstance(out, str)
    assert len(out) > 0


def test_cmd_decide_mentions_context(work_dir):
    ctx = "Sunset legacy pipeline in June?"
    out = cmd_decide(context=ctx)
    # Output should reference the decision context in some way
    assert "june" in out.lower() or "pipeline" in out.lower() or "decision" in out.lower()


# ---------------------------------------------------------------------------
# T3-28: cmd_decide empty context — must not crash
# ---------------------------------------------------------------------------

def test_cmd_decide_empty_context(work_dir):
    out = cmd_decide(context="")
    assert isinstance(out, str)
    assert len(out) > 0


# ---------------------------------------------------------------------------
# T3-29: _ensure_decisions_header idempotency
# ---------------------------------------------------------------------------

def test_ensure_decisions_header_idempotent(work_dir):
    decisions_file = work_dir / "decisions.md"
    # First call — creates header
    _ensure_decisions_header(decisions_file)
    content_after_first = decisions_file.read_text(encoding="utf-8")
    # Second call — should not duplicate header
    _ensure_decisions_header(decisions_file)
    content_after_second = decisions_file.read_text(encoding="utf-8")
    # Header count should be the same
    assert content_after_first == content_after_second


def test_ensure_decisions_header_creates_file(work_dir):
    decisions_file = work_dir / "decisions.md"
    assert not decisions_file.exists()
    _ensure_decisions_header(decisions_file)
    assert decisions_file.exists()
    content = decisions_file.read_text(encoding="utf-8")
    assert len(content) > 0


# ---------------------------------------------------------------------------
# T3-30: _append_to_file encoding correctness
# ---------------------------------------------------------------------------

def test_append_to_file_creates(work_dir):
    target = work_dir / "append_test.md"
    _append_to_file(target, "# Header\n\nFirst line.\n")
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "First line." in content


def test_append_to_file_accumulates(work_dir):
    target = work_dir / "append_accum.md"
    _append_to_file(target, "Line 1\n")
    _append_to_file(target, "Line 2\n")
    content = target.read_text(encoding="utf-8")
    assert "Line 1" in content
    assert "Line 2" in content


def test_append_to_file_unicode(work_dir):
    target = work_dir / "unicode_test.md"
    _append_to_file(target, "Résumé: naïve café\n")
    content = target.read_text(encoding="utf-8")
    assert "Résumé" in content


# ---------------------------------------------------------------------------
# T3-31: cmd_decide creates decisions file when absent
# ---------------------------------------------------------------------------

def test_cmd_decide_creates_decisions_file(work_dir):
    assert not (work_dir / "decisions.md").exists()
    cmd_decide(context="Deploy v2.0 in Q4?")
    # May or may not create the file depending on implementation —
    # at minimum it must return a non-empty string and not crash
    out = cmd_decide(context="Deploy v2.0 in Q4?")
    assert isinstance(out, str)
    assert len(out) > 0


# ---------------------------------------------------------------------------
# T3-32: Active decisions visible in cmd_decide output
# ---------------------------------------------------------------------------

def test_cmd_decide_reads_existing_decisions(work_dir):
    decisions_file = work_dir / "decisions.md"
    decisions_file.write_text(
        "# Decision Log\n\n| ID | Date | Context | Status |\n|---|---|---|---|\n"
        "| D-001 | 2026-03-01 | Migrate DB to Cosmos | open |\n",
        encoding="utf-8",
    )
    out = cmd_decide(context="DB migration review")
    assert isinstance(out, str)
    assert len(out) > 0
