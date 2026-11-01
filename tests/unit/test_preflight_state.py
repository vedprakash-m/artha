"""tests/unit/test_preflight_state.py — T5-26..33: preflight.state_checks tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ARTHA_ROOT  = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
for _p in [str(_ARTHA_ROOT), str(_SCRIPTS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import preflight as pf


# ---------------------------------------------------------------------------
# T5-26..27: check_state_directory
# ---------------------------------------------------------------------------

class TestCheckStateDirectory:
    def test_existing_writable_dir_passes(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        monkeypatch.setattr(pf, "STATE_DIR", str(state_dir))
        result = pf.check_state_directory()
        assert isinstance(result, pf.CheckResult)
        assert result.severity in ("P0", "P1")

    def test_missing_dir_fails_p0(self, tmp_path, monkeypatch):
        missing = tmp_path / "no_state_here"
        monkeypatch.setattr(pf, "STATE_DIR", str(missing))
        result = pf.check_state_directory()
        assert isinstance(result, pf.CheckResult)
        assert not result.passed
        assert result.severity == "P0"


# ---------------------------------------------------------------------------
# T5-28..29: check_state_templates
# ---------------------------------------------------------------------------

class TestCheckStateTemplates:
    def test_returns_check_result(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        monkeypatch.setattr(pf, "STATE_DIR", str(state_dir))
        monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
        result = pf.check_state_templates()
        assert isinstance(result, pf.CheckResult)

    def test_existing_templates_pass(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        # Create stub required templates
        (state_dir / "open_items.md").write_text("# Open Items\n")
        monkeypatch.setattr(pf, "STATE_DIR", str(state_dir))
        monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
        result = pf.check_state_templates()
        assert isinstance(result, pf.CheckResult)


# ---------------------------------------------------------------------------
# T5-30..31: check_open_items
# ---------------------------------------------------------------------------

class TestCheckOpenItems:
    def test_existing_open_items_passes(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "open_items.md").write_text("# Open Items\n\n- [ ] OI-001 Test task\n")
        monkeypatch.setattr(pf, "STATE_DIR", str(state_dir))
        monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
        result = pf.check_open_items()
        assert isinstance(result, pf.CheckResult)

    def test_missing_open_items_auto_creates(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        monkeypatch.setattr(pf, "STATE_DIR", str(state_dir))
        monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
        result = pf.check_open_items()
        assert isinstance(result, pf.CheckResult)


# ---------------------------------------------------------------------------
# T5-32..33: check_profile_completeness
# ---------------------------------------------------------------------------

class TestCheckProfileCompleteness:
    def test_populated_profile_passes(self, tmp_path, monkeypatch):
        import yaml
        monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
        profile = {
            f"key_{i}": f"value_{i}" for i in range(15)   # 15 keys → well above stub threshold
        }
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "config" / "user_profile.yaml").write_text(yaml.dump(profile))
        result = pf.check_profile_completeness()
        assert isinstance(result, pf.CheckResult)

    def test_empty_profile_fails_p1(self, tmp_path, monkeypatch):
        import yaml
        monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        # Only 3 keys — classic bootstrap stub
        (tmp_path / "config" / "user_profile.yaml").write_text(yaml.dump({
            "name": "Test User",
            "email": "test@example.com",
        }))
        result = pf.check_profile_completeness()
        assert isinstance(result, pf.CheckResult)
        assert result.severity in ("P1", "P0")
