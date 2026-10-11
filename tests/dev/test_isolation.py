"""WP-00 hermetic-test contract (WP00.AC1-AC3).

These tests describe the isolation guarantees that ``tests/conftest.py`` must
install before any other test module is imported.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

import _isolation as iso


def test_home_is_isolated_from_real_home():
    real_home = Path(os.environ["ARTHA_TEST_REAL_HOME"])
    assert Path.home() != real_home
    assert real_home not in Path.home().parents
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
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        client = socket.create_connection(server.getsockname(), timeout=2)
        client.close()
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
