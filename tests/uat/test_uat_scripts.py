"""Runs every tools/uat scenario script in --scratch mode and checks its report."""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((ROOT / "tools" / "uat").glob("ra_*.py"))


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=600,
                          check=False)


def test_every_requirement_has_a_script() -> None:
    found = {p.name.split("_")[1] for p in SCRIPTS}
    assert found == {"06", "09", "15", "19", "20", "21", "23"}


@pytest.mark.parametrize("script", SCRIPTS, ids=[p.stem for p in SCRIPTS])
def test_scratch_run_passes(script: Path, tmp_path: Path) -> None:
    result = _run(str(script), "--scratch", "--workspace", str(tmp_path))
    report = json.loads(result.stdout)
    failed = [s for s in report["steps"] if not s["pass"]]
    assert result.returncode == 0, (failed, result.stderr[-2000:])
    assert report["pass"] is True
    assert report["requirement"] == f"RA-{script.name.split('_')[1]}"
    assert report["run_at"]
    assert report["steps"]
    for step in report["steps"]:
        assert {"command", "expected", "observed", "pass"} <= step.keys()
    assert list(tmp_path.iterdir()) == []


WP19 = ROOT / "tools" / "uat" / "wp_19_work_cache.py"


def _age_keygen_available() -> bool:
    import os
    import shutil
    return bool(shutil.which("age-keygen")) or any(
        os.access(p, os.X_OK) for p in ("/opt/homebrew/bin/age-keygen", "/usr/local/bin/age-keygen"))


@pytest.mark.skipif(not _age_keygen_available(), reason="age-keygen unavailable")
def test_work_cache_migration_scratch_run_passes_and_leaves_nothing(tmp_path: Path) -> None:
    result = _run(str(WP19), "--scratch", "--workspace", str(tmp_path))
    report = json.loads(result.stdout)
    failed = [s for s in report["steps"] if not s["pass"]]
    assert result.returncode == 0, (failed, result.stderr[-2000:])
    assert report["pass"] is True and report["requirement"] == "WP-19"
    commands = " ".join(s["command"] for s in report["steps"])
    for row in ("plan", "apply --sha256 0000", "--writers-stopped"):
        assert row in commands
    health_steps = [s for s in report["steps"] if "WorkIQ health receipt" in s["command"]]
    assert len(health_steps) == 6 and all(s["pass"] for s in health_steps)
    assert any("not owner UAT" in limitation for limitation in report["limitations"])
    assert "synthetic work narrative" not in result.stdout
    assert "Synthetic Person" not in result.stdout
    assert "synthetic.person@example.invalid" not in result.stdout
    assert list(tmp_path.iterdir()) == []


def _load_wp19():
    spec = importlib.util.spec_from_file_location("wp19_uat", WP19)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_work_health_receipt_runs_production_writer_with_synthetic_probe(tmp_path: Path, monkeypatch) -> None:
    from lib import workiq_health

    script = _load_wp19()
    report = script.h.Report("WP-19", "health receipt", "scratch")
    write_receipt = workiq_health.write_receipt
    writes = []

    def observe(path, *, available, auth_valid):
        import keyring
        import winreg

        assert "WP19_SYNTHETIC_SECRET" not in os.environ
        assert Path(os.environ["HOME"]).is_relative_to(tmp_path)
        assert os.environ["PATH"] == ""
        with pytest.raises(AssertionError):
            keyring.get_password("synthetic", "synthetic")
        with pytest.raises(AssertionError):
            socket.socket()
        with pytest.raises(AssertionError):
            subprocess.Popen(["synthetic-npx"])
        with pytest.raises(OSError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, "synthetic")
        writes.append((path, available, auth_valid))
        return write_receipt(path, available=available, auth_valid=auth_valid)

    monkeypatch.setattr(workiq_health, "write_receipt", observe)
    monkeypatch.setenv("WP19_SYNTHETIC_SECRET", "synthetic-only")
    original_env = dict(os.environ)
    original_run, original_platform, original_socket = subprocess.run, platform.system, socket.socket
    missing = object()
    original_keyring = sys.modules.get("keyring", missing)
    original_registry = sys.modules.get("winreg", missing)
    before = datetime.now(timezone.utc)
    script.health_receipt_scenario(tmp_path, report)
    after = datetime.now(timezone.utc)

    assert report.result()["pass"], report.steps
    assert [(available, auth_valid) for _, available, auth_valid in writes] == [
        (True, True), (True, False), (False, False),
        (True, True), (True, False), (False, False),
    ]
    for path, available, auth_valid in writes:
        assert path.is_relative_to(tmp_path) and path.name == "health.json"
        assert path.parent.name == "cache" and path.parent.parent.name == "work"
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
        assert set(payload) == {"available", "auth_valid", "platform", "checked_at"}
        assert payload["available"] is available and payload["auth_valid"] is auth_valid
        assert payload["platform"] == "Windows"
        checked_at = datetime.fromisoformat(payload["checked_at"])
        assert checked_at.utcoffset() == timezone.utc.utcoffset(checked_at)
        assert before <= checked_at <= after
        assert "Synthetic Person" not in text and "synthetic.person@example.invalid" not in text
    assert dict(os.environ) == original_env
    assert subprocess.run is original_run and platform.system is original_platform
    assert socket.socket is original_socket
    assert sys.modules.get("keyring", missing) is original_keyring
    assert sys.modules.get("winreg", missing) is original_registry


def test_work_health_receipt_missing_writer_cannot_pass(tmp_path: Path, monkeypatch) -> None:
    from lib import workiq_health

    script = _load_wp19()
    report = script.h.Report("WP-19", "health receipt", "scratch")
    monkeypatch.setattr(workiq_health, "write_receipt", lambda *args, **kwargs: None)
    script.health_receipt_scenario(tmp_path, report)

    assert not report.result()["pass"]
    assert len(report.steps) == 6 and all(not step["pass"] for step in report.steps)
    assert not list(tmp_path.rglob("health.json"))


def test_work_health_receipt_leaking_identity_cannot_pass(tmp_path: Path, monkeypatch) -> None:
    from lib import workiq_health

    script = _load_wp19()
    report = script.h.Report("WP-19", "health receipt", "scratch")
    write_receipt = workiq_health.write_receipt

    def leak(path, *, available, auth_valid):
        write_receipt(path, available=available, auth_valid=auth_valid)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["user_name"] = "Synthetic Person"
        path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(workiq_health, "write_receipt", leak)
    script.health_receipt_scenario(tmp_path, report)

    assert not report.result()["pass"]
    assert len(report.steps) == 6 and all(not step["pass"] for step in report.steps)
    assert "Synthetic Person" not in json.dumps(report.result())


def test_work_cache_migration_script_refuses_without_scratch(tmp_path: Path) -> None:
    result = _run(str(WP19), "--workspace", str(tmp_path))
    assert result.returncode == 2
    assert "--scratch" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_live_mode_refuses_a_directory_that_is_not_a_checkout(tmp_path: Path) -> None:
    result = _run(str(SCRIPTS[0]), "--workspace", str(tmp_path))
    assert result.returncode != 0
    assert "not an Artha checkout" in result.stderr
