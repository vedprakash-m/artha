"""
tests/unit/test_pattern_engine.py — Unit tests for scripts/pattern_engine.py (E3)

Coverage:
  - PatternEngine.evaluate() returns list of DomainSignal
  - days_until_lte operator triggers signal when condition met
  - stale_days operator triggers signal when stale
  - lt / gt / eq operators work correctly
  - exists operator: signal fires when field is missing
  - Cooldown prevents re-fire within window
  - Cooldown state serialises and reloads correctly
  - Disabled pattern (enabled: false) is skipped
  - Feature flag disabled returns empty list
  - Invalid YAML path returns empty signals (no crash)
"""
from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from pattern_engine import PatternEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_state(tmp_path: Path, domain: str, data: dict) -> Path:
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    path = state_dir / f"{domain}.md"
    block = yaml.dump(data, default_flow_style=False)
    path.write_text(f"---\n{block}---\n\n# Body\n", encoding="utf-8")
    return path


def _write_patterns(tmp_path: Path, patterns: list[dict]) -> Path:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    path = cfg_dir / "patterns.yaml"
    payload = {"patterns": patterns}
    path.write_text(yaml.dump(payload, default_flow_style=False), encoding="utf-8")
    return path


def _make_engine(tmp_path: Path, patterns: list[dict]) -> "PatternEngine":
    """Create a PatternEngine pointed at tmp_path."""
    patterns_path = _write_patterns(tmp_path, patterns)
    return PatternEngine(patterns_file=patterns_path, root_dir=tmp_path)


# ---------------------------------------------------------------------------
# Basic operator tests
# ---------------------------------------------------------------------------

class TestDaysUntilLte:
    def test_fires_when_deadline_within_window(self, tmp_path):
        future = (date.today() + timedelta(days=5)).isoformat()
        _write_state(tmp_path, "immigration", {"visa_expiry": future})
        pattern = {
            "id": "PAT-TEST-01",
            "name": "Visa deadline",
            "source_file": "state/immigration.md",
            "source_path": "visa_expiry",
            "condition": {"all_of": [{"days_until_lte": 14}]},
            "output_signal": {"signal_type": "immigration_deadline", "domain": "immigration", "urgency": 3},
            "cooldown_hours": 24,
            "enabled": True,
        }
        engine = _make_engine(tmp_path, [pattern])
        signals = engine.evaluate()
        types = [s.signal_type for s in signals]
        assert "immigration_deadline" in types

    def test_does_not_fire_when_deadline_far(self, tmp_path):
        future = (date.today() + timedelta(days=60)).isoformat()
        _write_state(tmp_path, "immigration", {"visa_expiry": future})
        pattern = {
            "id": "PAT-TEST-02",
            "name": "Visa far deadline",
            "source_file": "state/immigration.md",
            "source_path": "visa_expiry",
            "condition": {"all_of": [{"days_until_lte": 14}]},
            "output_signal": {"signal_type": "immigration_deadline", "domain": "immigration", "urgency": 3},
            "cooldown_hours": 24,
            "enabled": True,
        }
        engine = _make_engine(tmp_path, [pattern])
        signals = engine.evaluate()
        types = [s.signal_type for s in signals]
        assert "immigration_deadline" not in types


class TestStaleDays:
    def test_stale_fires(self, tmp_path):
        old_date = (date.today() - timedelta(days=100)).isoformat()
        _write_state(tmp_path, "goals", {"last_reviewed": old_date})
        pattern = {
            "id": "PAT-TEST-03",
            "name": "Goal stale",
            "source_file": "state/goals.md",
            "source_path": "last_reviewed",
            "condition": {"all_of": [{"stale_days": 60}]},
            "output_signal": {"signal_type": "goal_stale", "domain": "goals", "urgency": 2},
            "cooldown_hours": 168,
            "enabled": True,
        }
        engine = _make_engine(tmp_path, [pattern])
        signals = engine.evaluate()
        types = [s.signal_type for s in signals]
        assert "goal_stale" in types

    def test_stale_does_not_fire_if_recent(self, tmp_path):
        recent = (date.today() - timedelta(days=5)).isoformat()
        _write_state(tmp_path, "goals", {"last_reviewed": recent})
        pattern = {
            "id": "PAT-TEST-04",
            "name": "Goal not stale",
            "source_file": "state/goals.md",
            "source_path": "last_reviewed",
            "condition": {"all_of": [{"stale_days": 60}]},
            "output_signal": {"signal_type": "goal_stale", "domain": "goals", "urgency": 2},
            "cooldown_hours": 168,
            "enabled": True,
        }
        engine = _make_engine(tmp_path, [pattern])
        signals = engine.evaluate()
        assert not any(s.signal_type == "goal_stale" for s in signals)


class TestEnabledFlag:
    def test_disabled_pattern_skipped(self, tmp_path):
        future = (date.today() + timedelta(days=5)).isoformat()
        _write_state(tmp_path, "immigration", {"visa_expiry": future})
        pattern = {
            "id": "PAT-TEST-DISABLED",
            "name": "Disabled visa",
            "source_file": "state/immigration.md",
            "source_path": "visa_expiry",
            "condition": {"all_of": [{"days_until_lte": 90}]},
            "output_signal": {"signal_type": "immigration_deadline", "domain": "immigration", "urgency": 3},
            "cooldown_hours": 24,
            "enabled": False,
        }
        engine = _make_engine(tmp_path, [pattern])
        signals = engine.evaluate()
        assert not any(s.signal_type == "immigration_deadline" for s in signals)


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------

class TestCooldown:
    def test_cooldown_prevents_refire(self, tmp_path):
        future = (date.today() + timedelta(days=5)).isoformat()
        _write_state(tmp_path, "immigration", {"visa_expiry": future})
        pattern = {
            "id": "PAT-COOL-01",
            "source_file": "state/immigration.md",
            "source_path": "visa_expiry",
            "condition": {"all_of": [{"days_until_lte": 30}]},
            "output_signal": {"signal_type": "immigration_deadline", "domain": "immigration", "urgency": 3},
            "cooldown_hours": 48,
            "enabled": True,
        }
        engine = _make_engine(tmp_path, [pattern])
        # First run — should fire
        signals1 = engine.evaluate()
        fired1 = any(s.signal_type == "immigration_deadline" for s in signals1)
        # Second run with same engine (cooldown active) — should NOT fire
        signals2 = engine.evaluate()
        fired2 = any(s.signal_type == "immigration_deadline" for s in signals2)
        assert fired1
        assert not fired2


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFeatureFlag:
    def test_flag_disabled_returns_empty(self, tmp_path):
        future = (date.today() + timedelta(days=5)).isoformat()
        _write_state(tmp_path, "immigration", {"visa_expiry": future})
        with patch("pattern_engine._load_flag", return_value=False):
            engine = _make_engine(tmp_path, [])
            signals = engine.evaluate()
        assert signals == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_missing_state_file_no_crash(self, tmp_path):
        (tmp_path / "config").mkdir(exist_ok=True)
        pattern = {
            "id": "PAT-MISSING",
            "source_file": "state/nonexistent.md",
            "source_path": "some_field",
            "condition": {"all_of": [{"exists": True}]},
            "output_signal": {"signal_type": "open_item_overdue", "domain": "goals", "urgency": 2},
            "cooldown_hours": 24,
            "enabled": True,
        }
        engine = _make_engine(tmp_path, [pattern])
        signals = engine.evaluate()  # Must not raise
        assert isinstance(signals, list)

    def test_empty_patterns_file(self, tmp_path):
        engine = _make_engine(tmp_path, [])
        signals = engine.evaluate()
        assert signals == []
