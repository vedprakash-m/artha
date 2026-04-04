"""tests/unit/test_checkpoint_wave1.py — Wave 1 tests for checkpoint.py

Wave 1 verification suite (specs/agent-fw.md §AFW-5).

Coverage:
  - write_checkpoint(): phase field is stored and read back
  - write_checkpoint(): connector_results dict is stored and read back
  - write_checkpoint(): domain_signals dict is stored and read back
  - write_checkpoint(): phase/connector_results/domain_signals are omitted when None
  - write_checkpoint(): backward compat — **metadata still works alongside new fields
  - _stale_hours(): config-driven stale hours override _MAX_AGE_HOURS fallback
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# conftest adds scripts/ to sys.path
from checkpoint import (
    _CHECKPOINT_FILE,
    _MAX_AGE_HOURS,
    _stale_hours,
    clear_checkpoint,
    read_checkpoint,
    write_checkpoint,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def artha_dir(tmp_path):
    """Minimal project tree that satisfies checkpoint feature-flag checks."""
    # Write a minimal config that enables checkpoints
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "artha_config.yaml").write_text(
        "harness:\n  agentic:\n    checkpoints:\n      enabled: true\n      stale_hours: 4\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# phase field
# ---------------------------------------------------------------------------

class TestPhaseField:
    def test_phase_stored(self, artha_dir):
        """phase is written to checkpoint and read back."""
        write_checkpoint(artha_dir, 3, phase="fetch")
        data = read_checkpoint(artha_dir)
        assert data is not None
        assert data["phase"] == "fetch"

    def test_all_phase_values(self, artha_dir):
        """All documented phase names roundtrip correctly."""
        for phase in ("preflight", "fetch", "process", "reason", "finalize"):
            write_checkpoint(artha_dir, 1, phase=phase)
            data = read_checkpoint(artha_dir)
            assert data["phase"] == phase

    def test_phase_none_excluded(self, artha_dir):
        """When phase=None (default), the key is absent from checkpoint JSON."""
        write_checkpoint(artha_dir, 1)
        data = read_checkpoint(artha_dir)
        assert data is not None
        assert "phase" not in data

    def test_phase_explicit_none_excluded(self, artha_dir):
        """Explicit phase=None also excludes the key."""
        write_checkpoint(artha_dir, 1, phase=None)
        data = read_checkpoint(artha_dir)
        assert data is not None
        assert "phase" not in data


# ---------------------------------------------------------------------------
# connector_results field
# ---------------------------------------------------------------------------

class TestConnectorResults:
    def test_connector_results_stored(self, artha_dir):
        """connector_results dict is written and read back."""
        cr = {"email": {"count": 12}, "calendar": {"events": 3}}
        write_checkpoint(artha_dir, 2, phase="fetch", connector_results=cr)
        data = read_checkpoint(artha_dir)
        assert data is not None
        assert data["connector_results"] == cr

    def test_connector_results_none_excluded(self, artha_dir):
        """connector_results=None (default) means key absent from checkpoint."""
        write_checkpoint(artha_dir, 2)
        data = read_checkpoint(artha_dir)
        assert data is not None
        assert "connector_results" not in data

    def test_connector_results_nested(self, artha_dir):
        """Nested dicts round-trip correctly via JSON serialization."""
        cr = {"email": {"count": 5, "labels": ["inbox", "travel"]}}
        write_checkpoint(artha_dir, 2, connector_results=cr)
        data = read_checkpoint(artha_dir)
        assert data["connector_results"]["email"]["labels"] == ["inbox", "travel"]


# ---------------------------------------------------------------------------
# domain_signals field
# ---------------------------------------------------------------------------

class TestDomainSignals:
    def test_domain_signals_stored(self, artha_dir):
        """domain_signals dict is written and read back."""
        ds = {"finance": ["tax_due", "payment_overdue"], "health": ["appointment_tomorrow"]}
        write_checkpoint(artha_dir, 4, phase="process", domain_signals=ds)
        data = read_checkpoint(artha_dir)
        assert data is not None
        assert data["domain_signals"] == ds

    def test_domain_signals_none_excluded(self, artha_dir):
        """domain_signals=None (default) means key absent from checkpoint."""
        write_checkpoint(artha_dir, 4)
        data = read_checkpoint(artha_dir)
        assert data is not None
        assert "domain_signals" not in data

    def test_all_three_fields_together(self, artha_dir):
        """All three new fields can be written and read back simultaneously."""
        write_checkpoint(
            artha_dir,
            5,
            phase="process",
            connector_results={"email": {"count": 10}},
            domain_signals={"finance": ["tax_due"]},
        )
        data = read_checkpoint(artha_dir)
        assert data["phase"] == "process"
        assert data["connector_results"] == {"email": {"count": 10}}
        assert data["domain_signals"] == {"finance": ["tax_due"]}


# ---------------------------------------------------------------------------
# Backward compatibility: **metadata still works
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_legacy_metadata_still_works(self, artha_dir):
        """**metadata kwargs still accepted alongside new explicit params."""
        write_checkpoint(artha_dir, 7, phase="fetch", email_count=42, domains=["finance"])
        data = read_checkpoint(artha_dir)
        assert data is not None
        assert data["phase"] == "fetch"
        assert data["email_count"] == 42
        assert data["domains"] == ["finance"]

    def test_last_step_always_present(self, artha_dir):
        """last_step key is always in checkpoint dict."""
        write_checkpoint(artha_dir, 3, phase="fetch")
        data = read_checkpoint(artha_dir)
        assert data["last_step"] == 3

    def test_timestamp_always_present(self, artha_dir):
        """timestamp key is always in checkpoint dict."""
        write_checkpoint(artha_dir, 3)
        data = read_checkpoint(artha_dir)
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# _stale_hours(): config-driven TTL
# ---------------------------------------------------------------------------

class TestStaleHoursConfig:
    def test_fallback_when_no_config(self, tmp_path):
        """Without config, _stale_hours() falls back to _MAX_AGE_HOURS."""
        # tmp_path has no config dir at all
        result = _stale_hours(tmp_path)
        assert result == float(_MAX_AGE_HOURS)

    def test_reads_from_config(self, tmp_path):
        """_stale_hours() reads stale_hours from artha_config.yaml."""
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "artha_config.yaml").write_text(
            "harness:\n  agentic:\n    checkpoints:\n      stale_hours: 8\n",
            encoding="utf-8",
        )
        result = _stale_hours(tmp_path)
        assert result == 8.0

    def test_stale_checkpoint_honoured_by_config(self, tmp_path):
        """Checkpoint aged 2h is stale when config sets stale_hours=1."""
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "artha_config.yaml").write_text(
            "harness:\n  agentic:\n    checkpoints:\n      enabled: true\n      stale_hours: 1\n",
            encoding="utf-8",
        )
        # Write a checkpoint with a timestamp 2 hours ago
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        cp_path = tmp_path / "tmp" / ".checkpoint.json"
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        cp_path.write_text(
            json.dumps({"last_step": 5, "timestamp": stale_ts}),
            encoding="utf-8",
        )
        # read_checkpoint should return None because it's > 1h stale
        result = read_checkpoint(tmp_path)
        assert result is None

    def test_fresh_checkpoint_with_short_stale(self, tmp_path):
        """A just-written checkpoint is fresh even when stale_hours=1."""
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "artha_config.yaml").write_text(
            "harness:\n  agentic:\n    checkpoints:\n      enabled: true\n      stale_hours: 1\n",
            encoding="utf-8",
        )
        write_checkpoint(tmp_path, 5, phase="fetch")
        result = read_checkpoint(tmp_path)
        assert result is not None
        assert result["last_step"] == 5
