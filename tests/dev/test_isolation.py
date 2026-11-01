"""WP-00 hermetic-test contract (WP00.AC1-AC3).

These tests describe the isolation guarantees that ``tests/conftest.py`` must
install before any other test module is imported.
"""
from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path

import pytest

import _isolation as iso


def test_home_is_isolated_from_real_home():
    real_home = Path(os.environ["ARTHA_TEST_REAL_HOME"])
    assert Path.home() != real_home
    assert Path.home().parent.resolve() == Path(tempfile.gettempdir()).resolve()
    assert Path.home().name.startswith('artha-test-home-')
    assert Path(os.path.expanduser("~")) == Path.home()


def test_local_dir_points_into_isolated_home():
    assert Path(os.environ["ARTHA_LOCAL_DIR"]).is_relative_to(Path.home())


def test_keyring_backend_is_null():
    assert os.environ["PYTHON_KEYRING_BACKEND"] == "keyring.backends.null.Keyring"


def test_credential_env_vars_are_scrubbed():
    leaked = [k for k in os.environ if iso.looks_like_credential_env(k)]
    assert leaked == []


def test_external_connect_is_blocked():
    with pytest.raises(iso.NetworkAccessError):
        socket.create_connection(("example.com", 80), timeout=1)


def test_external_dns_is_blocked():
    with pytest.raises(iso.NetworkAccessError):
        socket.getaddrinfo("example.com", 443)


def test_loopback_connect_is_allowed():
    server = socket.socket()
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
    except PermissionError as exc:
        pytest.skip(f"environment sandbox blocks loopback bind: {exc}")
    try:
        try:
            client = socket.create_connection(server.getsockname(), timeout=2)
            client.close()
        except PermissionError as exc:
            pytest.skip(f"environment sandbox blocks loopback connect: {exc}")
    finally:
        server.close()


@pytest.mark.parametrize(
    "host,allowed",
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("example.com", False),
        ("10.0.0.5", False),
        ("8.8.8.8", False),
    ],
)
def test_host_policy(host, allowed):
    assert iso.is_loopback_host(host) is allowed


def test_protected_snapshot_detects_modify_add_and_delete(tmp_path):
    (tmp_path / "state").mkdir()
    keep = tmp_path / "state" / "a.md"
    gone = tmp_path / "state" / "b.md"
    keep.write_text("one")
    gone.write_text("two")
    snap = iso.ProtectedSnapshot.take(tmp_path, ["state"])

    keep.write_text("changed")
    gone.unlink()
    (tmp_path / "state" / "c.md").write_text("new")

    changes = snap.diff()
    assert changes == {
        "modified": ["state/a.md"],
        "deleted": ["state/b.md"],
        "added": ["state/c.md"],
    }


def test_protected_snapshot_clean_when_untouched(tmp_path):
    (tmp_path / "briefings").mkdir()
    (tmp_path / "briefings" / "x.md").write_text("x")
    snap = iso.ProtectedSnapshot.take(tmp_path, ["briefings"])
    assert snap.diff() == {"modified": [], "deleted": [], "added": []}


def test_protected_snapshot_ignores_missing_roots(tmp_path):
    snap = iso.ProtectedSnapshot.take(tmp_path, ["state", "tmp"])
    assert snap.diff() == {"modified": [], "deleted": [], "added": []}


@pytest.mark.parametrize('module', ['EventKit', 'Contacts', 'ScriptingBridge'])
def test_native_household_apis_are_denied_before_collection(module):
    import importlib
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_minimal_child_environment_still_uses_the_null_credential_backend():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-c',
        "import os; print(os.environ.get('PYTHON_KEYRING_BACKEND'))"],
        env={}, capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == iso.NULL_KEYRING


def test_stat_diff_reports_a_change_made_after_the_marker_was_taken(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "a.md").write_text("one")
    snap = iso.ProtectedSnapshot.take(tmp_path, ["state"])
    marker = snap.stat_marker()
    assert snap.stat_diff(marker) == {"modified": [], "deleted": [], "added": []}
    (tmp_path / "state" / "a.md").write_text("longer text")
    (tmp_path / "state" / "new.md").write_text("x")
    assert snap.stat_diff(marker) == {"modified": ["state/a.md"], "deleted": [], "added": ["state/new.md"]}


def test_a_module_that_touches_a_production_path_fails_with_its_own_name(tmp_path):
    """Given a throw-away checkout copy, when one test module writes under state/, then that module errors."""
    import shutil
    import subprocess
    import sys
    repo = Path(__file__).resolve().parents[2]
    work = tmp_path / "checkout"
    (work / "tests").mkdir(parents=True)
    for name in ("conftest.py", "_isolation.py"):
        shutil.copy(repo / "tests" / name, work / "tests" / name)
    (work / "state").mkdir()
    (work / "state" / "keep.md").write_text("synthetic")
    (work / "scripts").mkdir()
    (work / "tests" / "dev").mkdir()
    (work / "tests" / "dev" / "test_writer.py").write_text(
        "from pathlib import Path\n\ndef test_writes():\n"
        "    (Path(__file__).resolve().parents[2] / 'state' / 'keep.md').write_text('changed by a test')\n")
    done = subprocess.run([sys.executable, "-m", "pytest", "tests/dev/test_writer.py", "-q", "-p", "no:cacheprovider"],
                          cwd=work, capture_output=True, text=True, timeout=120,
                          env={**os.environ, "ARTHA_ALLOW_INPLACE_TESTS": "1"})
    assert done.returncode != 0
    assert "test_writer changed production paths" in done.stdout and "state/keep.md" in done.stdout
