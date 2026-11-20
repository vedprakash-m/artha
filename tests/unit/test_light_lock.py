from __future__ import annotations

import json
import socket

import pytest

import vault
from preflight import vault_checks


def test_vault_never_probes_or_changes_foreign_session_marker(tmp_path, monkeypatch):
    marker = tmp_path / ".artha-decrypted"
    marker.write_text(
        json.dumps({"pid": 4242, "machine": "SYNTH-HOST-A", "timestamp": "2026-11-19T00:00:00Z"})
    )
    monkeypatch.setattr(vault, "LOCK_FILE", marker)
    monkeypatch.setattr(socket, "gethostname", lambda: "SYNTH-HOST-B")
    monkeypatch.setattr(
        vault,
        "_pid_running",
        lambda *_args: (_ for _ in ()).throw(AssertionError("foreign PID was probed")),
    )
    monkeypatch.setattr(
        vault,
        "do_encrypt",
        lambda: (_ for _ in ()).throw(AssertionError("foreign marker was auto-encrypted")),
    )

    assert vault.check_lock_state() == 2
    assert marker.exists()


def test_preflight_preserves_foreign_session_marker(tmp_path, monkeypatch):
    marker = tmp_path / ".artha-decrypted"
    marker.write_text(json.dumps({"pid": 4242, "machine": "SYNTH-HOST-A"}))
    monkeypatch.setattr(vault_checks, "LOCK_FILE", str(marker))
    monkeypatch.setattr(socket, "gethostname", lambda: "SYNTH-HOST-B")

    result = vault_checks.check_vault_lock(auto_fix=True)
    assert not result.passed
    assert result.severity == "P1"
    assert marker.exists()
    assert "not probed" in result.message


def test_auto_lock_preserves_foreign_marker(tmp_path, monkeypatch, capsys):
    marker = tmp_path / ".artha-decrypted"
    marker.write_text(json.dumps({"pid": 4242, "machine": "SYNTH-HOST-A"}))
    monkeypatch.setattr(vault, "LOCK_FILE", marker)
    monkeypatch.setattr(socket, "gethostname", lambda: "SYNTH-HOST-B")
    monkeypatch.setattr(
        vault,
        "do_encrypt",
        lambda: (_ for _ in ()).throw(AssertionError("foreign marker was encrypted")),
    )
    assert vault.do_auto_lock() == 2
    assert marker.exists()
    assert "did not probe" in capsys.readouterr().err


def test_preflight_clears_a_stale_pipeline_checkpoint_in_the_machine_local_dir(tmp_path, monkeypatch):
    import json

    from preflight import runtime_checks

    monkeypatch.setenv("ARTHA_LOCAL_DIR", str(tmp_path))
    monkeypatch.setattr(runtime_checks, "_READ_ONLY", False)
    checkpoint = tmp_path / "pipeline-checkpoint.json"
    checkpoint.write_text(json.dumps({"created_at": "2020-01-01T00:00:00+00:00"}), encoding="utf-8")
    runtime_checks._clear_stale_pipeline_checkpoint()
    assert not checkpoint.exists()


def test_a_marker_written_by_this_host_is_never_foreign(tmp_path, monkeypatch):
    # macOS reports a dotted name; the marker and the comparison must use the same identity.
    monkeypatch.setattr(socket, "gethostname", lambda: "SYNTH-HOST-A.local")
    marker = tmp_path / ".artha-decrypted"
    marker.write_text(json.dumps({"pid": 4242, "machine": "SYNTH-HOST-A.local"}))
    marker_host, current_host = vault._lock_host(json.loads(marker.read_text()))
    assert marker_host == current_host

    monkeypatch.setattr(vault_checks, "LOCK_FILE", str(marker))
    result = vault_checks.check_vault_lock(auto_fix=False)
    assert "Foreign-host" not in result.message


def test_host_id_is_the_short_stable_name(monkeypatch):
    from lib.light_mode import host_id

    monkeypatch.setattr(socket, "gethostname", lambda: "SYNTH-HOST-A.local")
    assert host_id() == "SYNTH-HOST-A"
    assert host_id("Other.example.net") == "Other"


def _vault_for_refusal_test(tmp_path):
    import sys

    from catchup_core.vault import AgeVault

    return AgeVault(tmp_path, identity=lambda: "x", recipient="age1x", age_bin=sys.executable)


@pytest.mark.parametrize("error_name", ["VaultUnavailable", "DestructiveWrite", "VaultConflict", "VaultUncertain"])
def test_every_refused_encrypted_write_is_recorded_for_the_catchup_watermark(tmp_path, monkeypatch, error_name):
    from catchup_core import vault as core_vault

    recorded = []
    monkeypatch.setattr(core_vault.AgeVault, "_record_state_refusal", staticmethod(lambda p, r: recorded.append(r)))
    sut = _vault_for_refusal_test(tmp_path)
    monkeypatch.setattr(sut, "_write", lambda *a, **k: (_ for _ in ()).throw(getattr(core_vault, error_name)("synthetic")))
    with pytest.raises(getattr(core_vault, error_name)):
        sut.write("finance", "text")
    assert recorded == ["synthetic"]


@pytest.mark.parametrize("error_name,code", [
    ("VaultConflict", 20), ("VaultUnavailable", 20), ("DestructiveWrite", 20), ("VaultUncertain", 30),
])
def test_vault_write_cli_maps_outcomes_to_the_documented_exit_codes(tmp_path, monkeypatch, error_name, code):
    import io
    import sys

    from catchup_core import vault as core_vault

    monkeypatch.setattr(vault, "get_public_key", lambda: "age1x")
    monkeypatch.setattr(vault, "get_private_key", lambda: "AGE-SECRET-KEY-x")
    monkeypatch.setattr(vault, "STATE_DIR", tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("text"))
    monkeypatch.setattr(core_vault.AgeVault, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(
        core_vault.AgeVault, "write",
        lambda self, *a, **k: (_ for _ in ()).throw(getattr(core_vault, error_name)("synthetic")),
    )
    with pytest.raises(SystemExit) as exit_info:
        vault.do_write("finance", expected_revision="")
    assert exit_info.value.code == code


def test_no_remediation_hint_points_at_the_refused_bulk_decrypt():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "scripts"
    offenders = []
    for relative in ("vault.py", "middleware/guardrails.py", "lib/exceptions.py"):
        for number, line in enumerate((root / relative).read_text(encoding="utf-8").splitlines(), 1):
            lowered = line.lower()
            if "run: python scripts/vault.py decrypt" in lowered or "re-run `python scripts/vault.py decrypt`" in lowered:
                offenders.append(f"{relative}:{number}")
    assert offenders == []
