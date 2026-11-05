"""The Windows CI environment must satisfy, not weaken, the production owner checks."""
from __future__ import annotations

import ctypes
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "windows_contract_launcher", ROOT / "tools" / "run_windows_contracts.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_parent_launches_only_the_isolated_child_and_propagates_exit(tmp_path, monkeypatch, capsys):
    seen = []

    class Native:
        def launch(self, argv):
            seen.append(argv)
            output = Path(argv[argv.index("--child-output") + 1])
            output.write_text("synthetic test output\n", encoding="utf-8")
            return 7

    monkeypatch.setattr(launcher, "_Native", Native)
    assert launcher.main(["tests/catchup_core", "-q"]) == 7
    assert seen[0][:2] == [sys.executable, str(ROOT / "tools" / "run_windows_contracts.py")]
    assert seen[0][-2:] == ["tests/catchup_core", "-q"]
    assert capsys.readouterr().out == "synthetic test output\n"
    assert not Path(seen[0][3]).exists()


@pytest.mark.parametrize("failure", ["duplicate", "adjust", "launch"])
def test_token_failures_never_fall_back_to_an_ordinary_launch(monkeypatch, capsys, failure):
    class Native:
        def launch(self, argv):
            raise OSError(f"synthetic {failure} failure")

    monkeypatch.setattr(launcher, "_Native", Native)
    monkeypatch.setattr(launcher.subprocess, "run",
                        lambda *a, **k: pytest.fail("unsafe fallback to ordinary token"))
    assert launcher.main(["tests/catchup_core"]) == 2
    assert failure in capsys.readouterr().err


def test_success_without_child_output_is_not_success(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "_Native",
                        lambda: SimpleNamespace(launch=lambda argv: 0))
    assert launcher.main(["tests/catchup_core"]) == 2
    assert "child output" in capsys.readouterr().err


def test_child_probes_before_running_the_isolated_runner(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "_Native",
                        lambda: SimpleNamespace(verify_producers=lambda: calls.append("probe")))

    def run(argv, **kwargs):
        assert calls == ["probe"]
        calls.append(argv)
        assert kwargs["stdout"] is kwargs["stderr"]
        assert kwargs["check"] is False
        return SimpleNamespace(returncode=3)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher._child(tmp_path / "output.txt", ["tests/catchup_core", "-q"]) == 3
    assert calls[1] == [
        sys.executable, str(ROOT / "tools" / "run_tests_isolated.py"), "tests/catchup_core", "-q"]


def test_child_probe_failure_does_not_run_tests(tmp_path, monkeypatch):
    def refuse():
        raise OSError("synthetic wrong owner")

    monkeypatch.setattr(launcher, "_Native",
                        lambda: SimpleNamespace(verify_producers=refuse))
    monkeypatch.setattr(launcher.subprocess, "run",
                        lambda *a, **k: pytest.fail("tests ran without owner verification"))
    output = tmp_path / "output.txt"
    assert launcher._child(output, ["tests/catchup_core"]) == 2
    assert "synthetic wrong owner" in output.read_text(encoding="utf-8")


def test_missing_child_output_argument_is_a_visible_usage_failure(capsys):
    assert launcher.main(["--child-output"]) == 2
    assert "missing child output path" in capsys.readouterr().err


def test_child_output_cannot_overwrite_an_existing_file(tmp_path, capsys):
    output = tmp_path / "existing"
    output.write_bytes(b"preserve")
    assert launcher.main(["--child-output", str(output)]) == 2
    assert output.read_bytes() == b"preserve"
    assert "refused" in capsys.readouterr().err


def test_native_launcher_refuses_non_windows_hosts(monkeypatch):
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="requires Windows"):
        launcher._Native()


def test_windows_ci_uses_mandatory_launcher_not_a_diagnostic_or_skip():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["core-windows"]["steps"]
    run = next(step for step in steps if "tools/run_windows_contracts.py" in step.get("run", ""))
    assert "tools/run_tests_isolated.py" not in run["run"]
    assert "tests/dev/test_windows_contract_launcher.py" in run["run"]
    assert run.get("continue-on-error", False) is False
    assert not any("owner-probe" in step.get("run", "") for step in steps)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows token and filesystem APIs")
def test_native_duplicate_has_user_owner_without_changing_parent_token():
    native = launcher._Native()
    with native.process_token() as original:
        before = native.information(original, 4)
        with native.duplicate_for_tests(original) as duplicate:
            assert native.same_sid(native.information(duplicate, 1), native.information(duplicate, 4))
        after = native.information(original, 4)
        assert native.same_sid(before, after)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows token APIs")
def test_native_adjustment_failure_keeps_parent_token_unchanged(monkeypatch):
    native = launcher._Native()
    with native.process_token() as original:
        before = native.information(original, 4)

        def refuse(*args):
            ctypes.set_last_error(5)
            return 0

        monkeypatch.setattr(native.security, "SetTokenInformation", refuse)
        with pytest.raises(OSError):
            with native.duplicate_for_tests(original):
                pytest.fail("an unadjusted token was admitted")
        assert native.same_sid(before, native.information(original, 4))


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows token APIs")
@pytest.mark.parametrize("failure", [None, "wait", "exit"])
def test_native_launch_uses_verified_duplicate_and_closes_process_handles(monkeypatch, failure):
    from ctypes import wintypes

    native = launcher._Native()
    events = []
    create_event = native.kernel.CreateEventW
    create_event.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    create_event.restype = wintypes.HANDLE
    args = [sys.executable, "synthetic script with spaces.py", "argument with spaces"]

    def create(token, logon, application, command, flags, environment, cwd, startup, process):
        assert native.same_sid(native.information(token, 1), native.information(token, 4))
        assert logon == flags == 0 and environment is None and cwd is None
        assert application == sys.executable
        assert command.value == subprocess.list2cmdline(args)
        assert startup._obj.cb == ctypes.sizeof(launcher._StartupInfo)
        events.extend([create_event(None, True, True, None), create_event(None, True, True, None)])
        assert all(events)
        process._obj.process, process._obj.thread = events
        return 1

    def exit_code(process, code):
        assert process == events[0]
        code._obj.value = 9
        return 1

    monkeypatch.setattr(native.security, "CreateProcessWithTokenW", create)
    monkeypatch.setattr(native.kernel, "GetExitCodeProcess", exit_code)
    if failure == "wait":
        monkeypatch.setattr(native.kernel, "WaitForSingleObject", lambda *args: 0xFFFFFFFF)
    elif failure == "exit":
        monkeypatch.setattr(native.kernel, "GetExitCodeProcess", lambda *args: 0)
    if failure:
        with pytest.raises(OSError):
            native.launch(args)
    else:
        assert native.launch(args) == 9
    get_handle = native.kernel.GetHandleInformation
    get_handle.argtypes, get_handle.restype = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL
    assert all(not get_handle(handle, ctypes.byref(wintypes.DWORD())) for handle in events)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows token APIs")
@pytest.mark.parametrize("operation", ["OpenProcessToken", "DuplicateTokenEx", "CreateProcessWithTokenW"])
def test_native_token_and_process_api_refusals_do_not_launch_tests(monkeypatch, operation):
    native = launcher._Native()

    def refuse(*args):
        ctypes.set_last_error(5)
        return 0

    monkeypatch.setattr(native.security, operation, refuse)
    with pytest.raises(OSError):
        native.launch([sys.executable, "must-not-run"])


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows token APIs")
def test_native_token_verification_and_probe_fail_closed(monkeypatch):
    native = launcher._Native()
    monkeypatch.setattr(native, "same_sid", lambda *args: False)
    with native.process_token() as original:
        with pytest.raises(OSError, match="duplicated token default owner"):
            with native.duplicate_for_tests(original):
                pytest.fail("unverified token admitted")
    with pytest.raises(OSError, match="test child default owner"):
        native.verify_producers()


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows token APIs")
def test_native_failed_metadata_and_token_information_are_not_success(tmp_path, monkeypatch):
    native = launcher._Native()
    monkeypatch.setattr(native.security, "GetNamedSecurityInfoW", lambda *args: 5)
    with pytest.raises(OSError):
        native.verify_owner(tmp_path, None)
    with native.process_token() as token:
        monkeypatch.setattr(native.security, "GetTokenInformation", lambda *args: 1)
        with pytest.raises(OSError, match="could not size"):
            native.information(token, 1)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows token APIs")
def test_native_owner_probe_refuses_a_mismatched_sid_without_changing_the_file(tmp_path):
    from ctypes import wintypes

    native = launcher._Native()
    create_sid = native.security.CreateWellKnownSid
    create_sid.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.POINTER(wintypes.DWORD)]
    create_sid.restype = wintypes.BOOL
    size = wintypes.DWORD(68)
    sid = ctypes.create_string_buffer(size.value)
    native.check(create_sid(1, None, sid, ctypes.byref(size)))  # World, never the creating user.
    different_user = ctypes.c_void_p(ctypes.addressof(sid))
    path = tmp_path / "synthetic"
    path.write_bytes(b"unchanged")
    with pytest.raises(OSError, match="not owned"):
        native.verify_owner(path, ctypes.byref(different_user))
    assert path.read_bytes() == b"unchanged"


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows token and filesystem APIs")
def test_native_child_probe_checks_file_directory_and_subprocess(tmp_path):
    launcher._Native().verify_producers()
    result = subprocess.run(
        [sys.executable, "-c",
         "import runpy; m=runpy.run_path(__import__('sys').argv[1]); "
         "m['_Native']().verify_producers()",
         str(ROOT / "tools" / "run_windows_contracts.py")],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
