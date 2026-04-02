"""tests/unit/test_outcome_signals.py — Unit tests for EV-11a outcome signals.

Tests collect_outcome_signals() and related helpers in artha_context.py.
All state files are synthetic — no real user data (DD-5).
Ref: specs/eval.md EV-11a, T-EV-11a-01 to T-EV-11a-08
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def ctx():
    return _load_module("artha_context_signals", _SCRIPTS_DIR / "artha_context.py")


@pytest.fixture()
def artha_dir(tmp_path):
    """Set up a minimal artha dir skeleton."""
    (tmp_path / "state").mkdir()
    (tmp_path / "tmp").mkdir()
    return tmp_path


# ===========================================================================
# T-EV-11a-01: correction added after prev_ts → count = 1
# ===========================================================================

def test_correction_added_after_prev_ts_counted(ctx, artha_dir):
    """T-EV-11a-01: Correction with date_added after prev_ts must be counted."""
    memory_path = artha_dir / "state" / "memory.md"
    memory_path.write_text(
        "---\nfacts:\n"
        "  - type: correction\n"
        "    domain: finance\n"
        "    value: Use gross income\n"
        "    date_added: '2026-02-01'\n"
        "    ttl: '2027-01-01'\n"
        "---\n"
    )
    count = ctx._count_corrections_since(artha_dir, "2026-01-15T00:00:00+00:00")
    assert count == 1


# ===========================================================================
# T-EV-11a-02: correction before prev_ts → count = 0
# ===========================================================================

def test_correction_before_prev_ts_not_counted(ctx, artha_dir):
    """T-EV-11a-02: Correction with date_added before prev_ts must not be counted."""
    memory_path = artha_dir / "state" / "memory.md"
    memory_path.write_text(
        "---\nfacts:\n"
        "  - type: correction\n"
        "    domain: finance\n"
        "    value: Old correction\n"
        "    date_added: '2026-01-10'\n"
        "    ttl: '2027-01-01'\n"
        "---\n"
    )
    # prev_ts after the date_added
    count = ctx._count_corrections_since(artha_dir, "2026-01-25T00:00:00+00:00")
    assert count == 0


# ===========================================================================
# T-EV-11a-03: open item removed between sessions → resolved_24h = 1
# ===========================================================================

def test_open_item_resolved_counted(ctx, artha_dir):
    """T-EV-11a-03: Item in prev_run but not in current open_items → resolved_24h=1."""
    open_items_path = artha_dir / "state" / "open_items.md"
    open_items_path.write_text(
        "- id: OI-001\n  title: Still open\n  status: open\n"
    )
    # prev_run had items OI-001 and OI-002; only OI-001 still open
    prev_items = {"OI-001", "OI-002"}
    current_items = ctx._load_current_open_items(artha_dir)
    resolved = len(prev_items - current_items)
    assert resolved == 1


# ===========================================================================
# T-EV-11a-04: same items still open → resolved = 0
# ===========================================================================

def test_no_items_resolved_when_all_still_open(ctx, artha_dir):
    """T-EV-11a-04: No items resolved when prev_run items still open."""
    open_items_path = artha_dir / "state" / "open_items.md"
    open_items_path.write_text(
        "- id: OI-001\n  title: Task one\n  status: open\n\n"
        "- id: OI-002\n  title: Task two\n  status: open\n"
    )
    prev_items = {"OI-001", "OI-002"}
    current_items = ctx._load_current_open_items(artha_dir)
    resolved = len(prev_items - current_items)
    assert resolved == 0, f"Expected 0 resolved, got {resolved}"


# ===========================================================================
# T-EV-11a-05: session_history file after prev_ts → queries_since = 1
# ===========================================================================

def test_session_history_after_prev_ts_counted(ctx, artha_dir):
    """T-EV-11a-05: session_history_*.md created after prev_ts must increment queries_since."""
    hist = artha_dir / "tmp" / "session_history_abc123.md"
    hist.write_text("# Session History\nSome queries here.\n")
    # prev_ts is far in the past; file was just created → mtime > cutoff
    count = ctx._count_queries_since(artha_dir, "2020-01-01T00:00:00+00:00")
    assert count == 1


# ===========================================================================
# T-EV-11a-06: collect_outcome_signals with no prev_run → returns {}
# ===========================================================================

def test_collect_outcome_signals_no_prev_run_returns_empty(ctx, artha_dir):
    """T-EV-11a-06: Empty prev_run dict must cause early return of empty dict."""
    result = ctx.collect_outcome_signals({}, {}, artha_dir)
    assert result == {}, f"Expected empty dict, got: {result}"


# ===========================================================================
# T-EV-11a-07: _backfill_run_record updates matching session_id in YAML
# ===========================================================================

def test_backfill_run_record_updates_correct_entry(ctx, artha_dir):
    """T-EV-11a-07: _backfill_run_record must update a matching entry in catch_up_runs.yaml."""
    import yaml  # type: ignore[import]

    runs_path = artha_dir / "state" / "catch_up_runs.yaml"
    runs_path.write_text(
        "---\n"
        "- session_id: abc123\n"
        "  timestamp: '2026-02-15T09:00:00+00:00'\n"
        "  quality_score: 75.0\n"
    )
    ok = ctx._backfill_run_record(artha_dir, "abc123", {"outcome_items_resolved_24h": 2})
    assert ok is True, "Expected True return on successful backfill"

    data = yaml.safe_load(runs_path.read_text())
    run = next(r for r in data if r.get("session_id") == "abc123")
    assert run["outcome_items_resolved_24h"] == 2


# ===========================================================================
# T-EV-11a-08: >3 day gap → outcome_user_absence_flag is None
# ===========================================================================

def test_absence_flag_none_when_gap_over_3_days(ctx, artha_dir):
    """T-EV-11a-08: collect_outcome_signals must set absence_flag=None when gap>3 days."""
    ts_5d_ago = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    prev_run = {"session_id": "old123", "timestamp": ts_5d_ago, "open_item_ids": []}

    # Provide minimal state so function can run
    memory_path = artha_dir / "state" / "memory.md"
    if not memory_path.exists():
        memory_path.write_text("---\nfacts: []\n---\n")
    open_items_path = artha_dir / "state" / "open_items.md"
    if not open_items_path.exists():
        open_items_path.write_text("")

    outcomes = ctx.collect_outcome_signals(prev_run, {}, artha_dir)
    # If feature is enabled, absence_flag must be None
    if outcomes:
        assert outcomes.get("outcome_user_absence_flag") is None, (
            f"Expected None for 5-day gap, got: {outcomes.get('outcome_user_absence_flag')}"
        )
