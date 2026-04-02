"""tests/unit/test_schema_version.py — Schema version consistency tests (DD-17).

Ensures schema_version="1.0.0" is present in output dicts of the eval layer.
Ref: specs/eval.md DD-17, T-DD17-01 to T-DD17-04
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
_ARTHA_DIR = Path(__file__).resolve().parent.parent.parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def scorer():
    return _load_module("eval_scorer_sv", _SCRIPTS_DIR / "eval_scorer.py")


@pytest.fixture(scope="module")
def log_digest_mod():
    return _load_module("log_digest_sv", _SCRIPTS_DIR / "log_digest.py")


@pytest.fixture(scope="module")
def hcw():
    return _load_module("health_check_writer_sv", _SCRIPTS_DIR / "health_check_writer.py")


# ===========================================================================
# T-DD17-01: score_briefing() result has schema_version == "1.0.0"
# ===========================================================================

def test_score_briefing_has_schema_version(scorer, tmp_path):
    """T-DD17-01: score_briefing() must include schema_version='1.0.0' in result."""
    briefing_file = tmp_path / "test_briefing.md"
    briefing_file.write_text(
        "# Daily Briefing\n"
        "- Review $500 tax payment due 2026-02-15\n"
        "- Finance: Investment returns are up 12% YTD\n"
    )
    result = scorer.score_briefing(str(briefing_file), artha_dir=str(_ARTHA_DIR))
    assert "schema_version" in result, "Missing schema_version in score_briefing() result"
    assert result["schema_version"] == "1.0.0", (
        f"Expected schema_version='1.0.0', got: {result['schema_version']}"
    )


# ===========================================================================
# T-DD17-02: build_digest() result has schema_version == "1.0.0"
# ===========================================================================

def test_build_digest_has_schema_version(log_digest_mod, tmp_path):
    """T-DD17-02: build_digest() must include schema_version='1.0.0' in result."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "connector.jsonl"
    log_file.write_text(
        json.dumps({"ts": "2026-01-01T00:00:00Z", "level": "INFO", "duration_ms": 100}) + "\n"
    )
    result = log_digest_mod.build_digest(log_dir=str(log_dir), window_days=30)
    assert "schema_version" in result, "Missing schema_version in build_digest() result"
    assert result["schema_version"] == "1.0.0", (
        f"Expected '1.0.0', got: {result['schema_version']}"
    )


# ===========================================================================
# T-DD17-03: catch_up_runs.yaml entry has schema_version == "1.0.0"
# ===========================================================================

def test_catch_up_runs_entry_has_schema_version(hcw, tmp_path):
    """T-DD17-03: _append_catch_up_run() must include schema_version='1.0.0' in YAML entry."""
    import yaml  # type: ignore[import]

    (tmp_path / "state").mkdir()
    runs_file = tmp_path / "state" / "catch_up_runs.yaml"
    runs_file.write_text("---\n[]\n")

    hcw._append_catch_up_run(
        artha_dir=tmp_path,
        session_id="test-sv-001",
        timestamp="2026-02-15T09:00:00+00:00",
        domains_processed=["finance"],
        connector_statuses={},
        token_budget_used=0,
        steps_completed=[],
        format_used="flash",
        hours_elapsed=8.0,
        compression_level="none",
        engagement_rate=0.5,
        items_surfaced=3,
        signals_shown=2,
        actions_accepted=1,
        self_model_overlays=[],
        items_resolved=0,
    )

    data = yaml.safe_load(runs_file.read_text())
    assert isinstance(data, list) and len(data) == 1
    entry = data[0]
    assert "schema_version" in entry, "Missing schema_version in catch_up_runs.yaml entry"
    assert entry["schema_version"] == "1.0.0"


# ===========================================================================
# T-DD17-04: collect_outcome_signals() result does NOT include schema_version
# ===========================================================================

def test_outcome_signals_no_schema_version(tmp_path):
    """T-DD17-04: collect_outcome_signals() dict must NOT include schema_version."""
    ctx = _load_module("artha_context_sv", _SCRIPTS_DIR / "artha_context.py")

    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)

    memory_path = tmp_path / "state" / "memory.md"
    memory_path.write_text("---\nfacts: []\n---\n")
    open_items_path = tmp_path / "state" / "open_items.md"
    open_items_path.write_text("")

    prev_run = {
        "session_id": "schema-test-001",
        "timestamp": "2026-02-10T09:00:00+00:00",
        "open_item_ids": [],
    }
    outcomes = ctx.collect_outcome_signals(prev_run, {}, tmp_path)
    if outcomes:
        assert "schema_version" not in outcomes, (
            "collect_outcome_signals() must NOT include schema_version"
        )
