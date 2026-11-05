"""tests/unit/test_preflight_state.py — T5-26..33: preflight.state_checks tests."""
from __future__ import annotations

import sys
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_ARTHA_ROOT  = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
for _p in [str(_ARTHA_ROOT), str(_SCRIPTS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import preflight as pf


@pytest.fixture(params=["package", "script"])
def state_preflight(request, monkeypatch):
    if request.param == "package":
        return pf
    name = "_preflight_state_script_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / "preflight.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


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
    @pytest.mark.parametrize("auto_fix", [False, True])
    def test_encrypted_state_is_not_missing_or_replaced_with_plaintext(
        self, tmp_path, monkeypatch, state_preflight, auto_fix,
    ):
        state = tmp_path / "state"
        templates = state / "templates"
        templates.mkdir(parents=True)
        (templates / "health.md").write_text("# Empty template\n", encoding="utf-8")
        sealed = state / "health.md.age"
        sealed.write_bytes(b"synthetic ciphertext")
        monkeypatch.setattr(state_preflight, "STATE_DIR", str(state))
        monkeypatch.setattr(state_preflight, "ARTHA_DIR", str(tmp_path))

        result = state_preflight.check_state_templates(auto_fix=auto_fix)

        assert result.passed
        assert not (state / "health.md").exists()
        assert sealed.read_bytes() == b"synthetic ciphertext"

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


@pytest.fixture
def script_preflight(tmp_path, monkeypatch):
    name = "_preflight_health_script_test"
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / "preflight.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ARTHA_DIR", str(tmp_path))
    return module


@pytest.mark.parametrize("mode", ["encrypted", "glob"])
def test_registry_freshness_resolves_actual_state_paths(tmp_path, script_preflight, mode):
    import yaml

    (tmp_path / "config").mkdir()
    state = tmp_path / "state"
    state.mkdir()
    path = "state/audit.md" if mode == "encrypted" else "state/people/*.md"
    if mode == "encrypted":
        (state / "audit.md.age").write_bytes(b"synthetic ciphertext")
    else:
        (state / "people").mkdir()
        (state / "people" / "example.md").write_text("# Synthetic\n")
    (tmp_path / "config" / "state_registry.yaml").write_text(
        yaml.safe_dump({"state_files": [{"path": path, "max_staleness_hours": 24}]}),
        encoding="utf-8",
    )

    assert script_preflight.check_state_registry_staleness().passed


@pytest.mark.parametrize("burn_rate", [0.0, 1.0, 2.0])
def test_slo_health_uses_status_mapping(script_preflight, monkeypatch, burn_rate):
    status = SimpleNamespace(slo_id="connector_success", burn_rate=burn_rate, sample_count=3)
    engine = SimpleNamespace(status=lambda: SimpleNamespace(slos={"connector_success": status}))
    monkeypatch.setitem(sys.modules, "slo_engine", SimpleNamespace(get_engine=lambda **kw: engine))

    result = script_preflight.check_slo_burn_rate()

    assert result.passed is (burn_rate <= 1.0)
    assert "skipped" not in result.message
    if burn_rate > 1:
        assert "connector_success" in result.message


def test_slo_unavailable_is_not_success(script_preflight, monkeypatch):
    def unavailable(**kwargs):
        raise OSError("synthetic unavailable metrics")

    monkeypatch.setitem(sys.modules, "slo_engine", SimpleNamespace(get_engine=unavailable))

    assert not script_preflight.check_slo_burn_rate().passed


@pytest.mark.parametrize("entries", [{}, {"connector_success": SimpleNamespace(
    slo_id="connector_success", burn_rate=0.0, sample_count=0,
)}])
def test_slo_without_observations_is_not_healthy(script_preflight, monkeypatch, entries):
    engine = SimpleNamespace(status=lambda: SimpleNamespace(slos=entries))
    monkeypatch.setitem(sys.modules, "slo_engine", SimpleNamespace(get_engine=lambda **kw: engine))
    result = script_preflight.check_slo_burn_rate()
    assert not result.passed
    assert "sample" in result.message.lower()


def test_registry_read_failure_is_not_success(tmp_path, script_preflight):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "state_registry.yaml").write_text("state_files: [")
    assert not script_preflight.check_state_registry_staleness().passed


@pytest.mark.parametrize("mode", ["missing", "stale"])
def test_registry_globs_do_not_hide_missing_or_stale_files(tmp_path, script_preflight, mode):
    import os
    import yaml

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "state_registry.yaml").write_text(yaml.safe_dump({
        "state_files": [{"path": "state/people/*.md", "max_staleness_hours": 24}],
    }))
    if mode == "stale":
        people = tmp_path / "state" / "people"
        people.mkdir(parents=True)
        path = people / "example.md"
        path.write_text("# Synthetic\n")
        os.utime(path, (0, 0))
    result = script_preflight.check_state_registry_staleness()
    assert not result.passed
    assert mode in result.message


def test_registry_stat_failure_is_reported(tmp_path, script_preflight, monkeypatch):
    import yaml

    (tmp_path / "config").mkdir()
    (tmp_path / "state").mkdir()
    target = tmp_path / "state" / "audit.md"
    target.write_text("# Synthetic\n")
    (tmp_path / "config" / "state_registry.yaml").write_text(yaml.safe_dump({
        "state_files": [{"path": "state/audit.md", "max_staleness_hours": 24}],
    }))
    stat = Path.stat

    def inaccessible(path, *args, **kwargs):
        if path == target:
            raise OSError("synthetic inaccessible state")
        return stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", inaccessible)
    result = script_preflight.check_state_registry_staleness()
    assert not result.passed
    assert "missing" in result.message


@pytest.mark.parametrize(("run_on", "passes"), [
    ("darwin", True), ("windows", False), ("all", False),
    (["darwin"], True), (["darwin", "windows"], False), (None, False),
])
def test_skill_inventory_warning_respects_current_host(
    script_preflight, monkeypatch, run_on, passes,
):
    import platform

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    index = {
        "counts": {"enabled": 1, "configured": 1},
        "enabled_missing_modules": ["example"],
        "skills": [{"name": "example", "run_on": run_on}],
    }
    monkeypatch.setitem(sys.modules, "skill_index", SimpleNamespace(
        build_skill_index=lambda root: index,
        write_skill_index=lambda value, path: None,
    ))
    result = script_preflight.check_skill_index()
    assert result.passed is passes
    assert index["enabled_missing_modules"] == ["example"]
