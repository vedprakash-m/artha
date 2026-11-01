"""Synthetic recovery metadata only; no native custody or provider access."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import time
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from catchup_core import recovery_registry as registry


@pytest.fixture
def tmp_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("recovery-registry")
    if os.name != "nt":
        return path
    from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory

    private = path / "private"
    with WindowsQuarantineDirectory.create(private, lock_file=False):
        pass
    return private


def reviewed_at():
    return datetime.now(timezone.utc).isoformat()


def enroll(root, profile="personal"):
    registry.declare(profile, "key", "synthetic", "version-1", "1", root=root)
    document = registry.snapshot(profile, root=root, require_review=False)
    return registry.attest_legacy(profile, registry.registry_digest(document),
                                  "synthetic reviewer", reviewed_at(), root=root)


def write_document(root, document):
    path = root / "recovery-registry" / "personal.json"
    path.write_text(json.dumps(document))
    path.chmod(0o600)
    return path


def set_everyone_readable_dacl(path):
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_handle = kernel32.CreateFileW
    open_handle.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    open_handle.restype = wintypes.HANDLE
    handle = open_handle(str(path), 0x00040000 | 0x00020000, 0x1 | 0x2, None,
                         3, 0x02000000 | 0x00200000, None)
    if handle in {None, wintypes.HANDLE(-1).value}:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    present = wintypes.BOOL()
    defaulted = wintypes.BOOL()
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                        ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    if not convert("D:P(A;;FA;;;OW)(A;;FA;;;SY)(A;;FR;;;WD)", 1,
                   ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        get_dacl = advapi32.GetSecurityDescriptorDacl
        get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                             ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
        get_dacl.restype = wintypes.BOOL
        if not get_dacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        set_security = advapi32.SetSecurityInfo
        set_security.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        set_security.restype = wintypes.DWORD
        result = set_security(handle, 1, 0x00000004 | 0x80000000, None, None, dacl, None)
        if result:
            raise OSError(result, "SetSecurityInfo failed")
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.LocalFree(descriptor)
        kernel32.CloseHandle(handle)


def test_absent_snapshot_does_not_enroll(tmp_path):
    root = tmp_path / "absent"
    with pytest.raises(registry.RegistryError):
        registry.snapshot("personal", root=root, require_review=False)
    assert not root.exists()


def test_declare_review_and_registered_write(tmp_path):
    document = enroll(tmp_path)
    assert document["schema"] == "artha.recovery-registry.v1"
    receipt = document["legacy_review"]
    assert receipt["host"] == document["host"]
    assert receipt["profile"] == "personal"
    with registry.mutation("personal", "credential", "synthetic", "account", "2", root=tmp_path):
        pending = registry.snapshot("personal", root=tmp_path, require_review=False)
        assert pending["entries"][-1]["state"] == "pending"
        with pytest.raises(registry.RegistryError):
            registry.snapshot("personal", root=tmp_path)
    complete = registry.snapshot("personal", root=tmp_path)
    assert complete["entries"][-1]["state"] == "active"
    assert complete["revision"] == document["revision"] + 2
    assert complete["legacy_review"] == receipt
    assert set(complete["entries"][-1]) == {
        "kind", "service", "account", "generation", "state", "attempt"}


def test_failure_retry_and_deleted_dependency(tmp_path):
    enroll(tmp_path)
    with pytest.raises(ValueError), registry.mutation(
            "personal", "key", "synthetic", "version-1", "2", root=tmp_path):
        raise ValueError("synthetic failure")
    with pytest.raises(registry.RegistryError):
        registry.snapshot("personal", root=tmp_path)
    with registry.mutation("personal", "key", "synthetic", "version-1", "2",
                           root=tmp_path, deleted=True):
        pass
    entries = registry.snapshot("personal", root=tmp_path)["entries"]
    assert len(entries) == 1
    assert entries[0]["state"] == "deleted"
    assert entries[0]["generation"] == "2"


def test_stale_attempt_cannot_complete_newer_intent(tmp_path):
    enroll(tmp_path)
    native_writes = []
    with registry.mutation("personal", "key", "synthetic", "same", "1", root=tmp_path):
        with pytest.raises(registry.RegistryError):
            with registry.mutation("personal", "key", "synthetic", "same", "2", root=tmp_path):
                native_writes.append("competing")
        native_writes.append("original")
    document = registry.snapshot("personal", root=tmp_path)
    assert native_writes == ["original"]
    assert document["entries"][-1]["generation"] == "1"
    assert document["entries"][-1]["state"] == "active"


@pytest.mark.parametrize("state", ["declared", "pending", "active"])
def test_conflicting_kind_refused_without_metadata_or_native_write(tmp_path, state):
    registry.declare("personal", "vault", "age-key", "artha", "1", root=tmp_path)
    if state != "declared":
        try:
            with registry.mutation("personal", "vault", "age-key", "artha", "1", root=tmp_path):
                if state == "pending":
                    raise ValueError("synthetic failure")
        except ValueError:
            pass
    before = registry.snapshot("personal", root=tmp_path, require_review=False)
    with pytest.raises(registry.RegistryError):
        registry.declare("personal", "client", "age-key", "artha", "1", root=tmp_path)
    with pytest.raises(registry.RegistryError):
        with registry.mutation("personal", "client", "age-key", "artha", "1", root=tmp_path):
            pytest.fail("conflicting kind reached native write")
    assert registry.snapshot("personal", root=tmp_path, require_review=False) == before


def test_duplicate_logical_identity_rejected_before_review(tmp_path):
    document = enroll(tmp_path)
    document["entries"].append({**document["entries"][0], "kind": "client"})
    write_document(tmp_path, document)
    with pytest.raises(registry.RegistryError):
        registry.snapshot("personal", root=tmp_path, require_review=False)
    with pytest.raises(registry.RegistryError):
        registry.attest_legacy("personal", "0" * 64, "reviewer", reviewed_at(), root=tmp_path)


def test_has_pending_is_read_only_and_does_not_enroll(tmp_path, monkeypatch):
    root = tmp_path / "absent"
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert not registry.has_pending("personal", "key", "synthetic", "account", "1", root=root)
    assert not root.exists()
    assert not registry.has_pending("personal", "key", "synthetic", "account", "1")
    assert not (tmp_path / ".artha-local").exists()
    enroll(root)
    before = {path.name: path.read_bytes() for path in (root / "recovery-registry").iterdir()}
    assert not registry.has_pending("personal", "key", "synthetic", "account", "1", root=root)
    assert {path.name: path.read_bytes() for path in (root / "recovery-registry").iterdir()} == before


def test_pending_intent_requires_matching_kind_and_generation(tmp_path):
    enroll(tmp_path)
    with pytest.raises(ValueError):
        with registry.mutation("personal", "client", "synthetic", "account", "1", root=tmp_path):
            assert registry.has_pending("personal", "client", "synthetic", "account", "1", root=tmp_path)
            raise ValueError("synthetic failure")
    before = registry.snapshot("personal", root=tmp_path, require_review=False)
    for kind, generation in (("bundle", "1"), ("client", "2")):
        with pytest.raises(registry.RegistryError):
            registry.has_pending("personal", kind, "synthetic", "account", generation, root=tmp_path)
        if kind != "client":
            with pytest.raises(registry.RegistryError):
                with registry.mutation("personal", kind, "synthetic", "account", generation, root=tmp_path):
                    pytest.fail("different dependency kind reached native write")
    assert registry.snapshot("personal", root=tmp_path, require_review=False) == before
    with registry.mutation("personal", "client", "synthetic", "account", "1", root=tmp_path):
        pass
    assert not registry.has_pending("personal", "client", "synthetic", "account", "1", root=tmp_path)
    assert not registry.has_pending("personal", "bundle", "synthetic", "account", "2", root=tmp_path)


def test_explicit_replacement_after_failed_writer_may_publish_a_new_generation(tmp_path):
    enroll(tmp_path)
    with pytest.raises(ValueError):
        with registry.mutation('personal', 'credential', 'synthetic', 'account', 'first', root=tmp_path):
            raise ValueError('failed publication')
    with registry.mutation('personal', 'credential', 'synthetic', 'account', 'replacement', root=tmp_path):
        entry = registry.snapshot('personal', root=tmp_path, require_review=False)['entries'][-1]
        assert entry['state'] == 'pending' and entry['generation'] == 'replacement'
    entry = registry.snapshot('personal', root=tmp_path)['entries'][-1]
    assert entry['state'] == 'active' and entry['generation'] == 'replacement'


@pytest.mark.parametrize("profile,service,account", [
    ("personal", "artha-work-checkpoint", ""), ("work", "synthetic", "account")])
def test_has_pending_rejects_cross_profile_before_creation(tmp_path, profile, service, account):
    root = tmp_path / "absent"
    with pytest.raises(registry.RegistryError):
        registry.has_pending(profile, "key", service, account, "1", root=root)
    assert not root.exists()


def test_nested_distinct_writes_keep_all_records(tmp_path):
    enroll(tmp_path)
    with registry.mutation("personal", "key", "synthetic", "first", "1", root=tmp_path):
        with registry.mutation("personal", "key", "synthetic", "second", "1", root=tmp_path):
            pass
    assert len(registry.snapshot("personal", root=tmp_path)["entries"]) == 3


def test_declaration_is_metadata_only_and_requires_fresh_review(tmp_path):
    enroll(tmp_path)
    document = registry.declare("personal", "vault", "historical", "", "legacy", root=tmp_path)
    assert document["entries"][-1]["state"] == "declared"
    assert document["legacy_review"] is None
    with pytest.raises(registry.RegistryError):
        registry.snapshot("personal", root=tmp_path)
    assert registry.declare("personal", "vault", "historical", "", "legacy", root=tmp_path) == document
    with pytest.raises(registry.RegistryError):
        registry.declare("personal", "vault", "historical", "", "different", root=tmp_path)


def test_review_rejects_stale_digest_pending_and_invalid_times(tmp_path):
    document = enroll(tmp_path)
    with pytest.raises(registry.RegistryError):
        registry.attest_legacy("personal", "0" * 64, "reviewer", reviewed_at(), root=tmp_path)
    for timestamp in ("2020-01-01T00:00:00", "invalid",
                      (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()):
        with pytest.raises(registry.RegistryError):
            registry.attest_legacy("personal", registry.registry_digest(document),
                                   "reviewer", timestamp, root=tmp_path)
    with registry.mutation("personal", "client", "synthetic", "pending", "1", root=tmp_path):
        pending = registry.snapshot("personal", root=tmp_path, require_review=False)
        with pytest.raises(registry.RegistryError):
            registry.attest_legacy("personal", registry.registry_digest(pending),
                                   "reviewer", reviewed_at(), root=tmp_path)


@pytest.mark.parametrize("service,account", [
    ("artha-work-checkpoint", ""), ("artha-credential-recovery-work", ""),
    ("artha.credentials.work.v1", ""), ("msgraph-work-client-id", ""),
    ("work.logical", ""), ("synthetic", "work.logical"),
    ("artha", "msgraph.work"), ("artha", "msgraph.work-mcp")])
def test_work_profiles(service, account, tmp_path):
    assert registry.profile_for(service, account) == "work"
    with pytest.raises(registry.RegistryError):
        registry.declare("personal", "key", service, account, "1", root=tmp_path)
    registry.declare("work", "key", service, account, "1", root=tmp_path)


def test_shared_age_and_default_root(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert registry.profile_for("age-key") == "personal"
    for profile in ("personal", "work"):
        registry.declare(profile, "key", "age-key", "artha", "1")
        assert (tmp_path / ".artha-local" / "recovery-registry" / f"{profile}.json").is_file()


@pytest.mark.parametrize("service,account", [
    ("synthetic", ""), ("artha", "msgraph.personal"), ("age-key", ""),
    ("age-key", "other"), ("age-key-other", "artha")])
def test_work_registry_refuses_personal_dependencies(service, account, tmp_path):
    root = tmp_path / "absent"
    with pytest.raises(registry.RegistryError):
        registry.declare("work", "key", service, account, "1", root=root)
    assert not root.exists()


def test_work_registry_rejects_personal_metadata_on_load(tmp_path):
    document = registry.declare("work", "credential", "artha", "msgraph.work", "1", root=tmp_path)
    document["entries"][0]["account"] = "msgraph.personal"
    path = tmp_path / "recovery-registry" / "work.json"
    path.write_text(json.dumps(document))
    with pytest.raises(registry.RegistryError):
        registry.snapshot("work", root=tmp_path, require_review=False)


@pytest.mark.parametrize("field", ["service", "account"])
@pytest.mark.parametrize("value", [
    "part/other", "part\\other", "part\x00other", "part\nother", "part\x7fother", "part\x85other"])
def test_unsafe_identity_rejected_before_creation_and_on_load(field, value, tmp_path):
    identity = {"service": "synthetic", "account": "account"}
    identity[field] = value
    root = tmp_path / "absent"
    with pytest.raises(registry.RegistryError):
        registry.declare("personal", "key", **identity, generation="1", root=root)
    assert not root.exists()
    document = enroll(tmp_path)
    document["entries"][0][field] = value
    write_document(tmp_path, document)
    with pytest.raises(registry.RegistryError):
        registry.snapshot("personal", root=tmp_path, require_review=False)


@pytest.mark.parametrize("field,value", [
    ("schema", "wrong"), ("profile", "work"), ("host", "other-host"),
    ("revision", True), ("revision", -1), ("entries", {}),
    ("legacy_review", {}), ("extra", "secret-like unexpected field")])
def test_rejects_malformed_documents(field, value, tmp_path):
    document = enroll(tmp_path)
    document[field] = value
    write_document(tmp_path, document)
    with pytest.raises(registry.RegistryError):
        registry.snapshot("personal", root=tmp_path, require_review=False)
    with pytest.raises(registry.RegistryError):
        registry.has_pending("personal", "key", "synthetic", "version-1", "1", root=tmp_path)


@pytest.mark.parametrize("field,value", [
    ("kind", "unknown"), ("generation", ""), ("state", "unknown"),
    ("attempt", 1), ("account", None), ("extra", "not metadata")])
def test_rejects_malformed_entries(field, value, tmp_path):
    document = enroll(tmp_path)
    document["entries"][0][field] = value
    write_document(tmp_path, document)
    with pytest.raises(registry.RegistryError):
        registry.snapshot("personal", root=tmp_path, require_review=False)


def test_duplicate_keys_identities_and_size_bound(tmp_path):
    document = enroll(tmp_path)
    path = tmp_path / "recovery-registry" / "personal.json"
    path.write_text('{"schema":"first","schema":"second"}')
    with pytest.raises(registry.RegistryError):
        registry.snapshot("personal", root=tmp_path, require_review=False)
    document["entries"].append(dict(document["entries"][0]))
    write_document(tmp_path, document)
    with pytest.raises(registry.RegistryError):
        registry.snapshot("personal", root=tmp_path, require_review=False)
    path.write_bytes(b" " * (4 * 1024 * 1024 + 1))
    with pytest.raises(registry.RegistryError):
        registry.snapshot("personal", root=tmp_path, require_review=False)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and link fixtures")
@pytest.mark.parametrize("operation", ["declare", "mutation", "has_pending"])
@pytest.mark.parametrize("target,link_kind", [
    ("root", "symlink"), ("directory", "symlink"), ("file", "symlink"),
    ("lock", "symlink"), ("temporary", "symlink"), ("installation", "symlink"),
    ("file", "hardlink"), ("lock", "hardlink"), ("temporary", "hardlink"),
    ("installation", "hardlink")])
def test_rejects_links_before_write(target, link_kind, operation, tmp_path):
    root = tmp_path / "local"
    enroll(root)
    paths = {"root": root, "directory": root / "recovery-registry",
             "file": root / "recovery-registry" / "personal.json",
             "lock": root / "recovery-registry" / "personal.lock",
             "temporary": root / "recovery-registry" / "personal.json.tmp",
             "installation": root / "installation.lock"}
    path = paths[target]
    destination = tmp_path / "outside"
    if path.exists():
        path.rename(destination)
    else:
        destination.write_text("untouched")
    destination.chmod(0o755 if destination.is_dir() else 0o644)
    original_mode = stat.S_IMODE(destination.stat().st_mode)
    if link_kind == "symlink":
        path.symlink_to(destination, target_is_directory=destination.is_dir())
    else:
        os.link(destination, path)
    with pytest.raises(registry.RegistryError):
        if operation == "declare":
            registry.declare("personal", "key", "synthetic", "new", "1", root=root)
        elif operation == "has_pending":
            registry.has_pending("personal", "key", "synthetic", "new", "1", root=root)
        else:
            with registry.mutation("personal", "key", "synthetic", "new", "1", root=root):
                pytest.fail("unsafe path entered caller")
    assert stat.S_IMODE(destination.stat().st_mode) == original_mode


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode enforcement")
@pytest.mark.parametrize("operation", ["declare", "mutation", "has_pending"])
@pytest.mark.parametrize("target", ["root", "directory", "file", "lock", "temporary", "installation"])
def test_rejects_public_permissions(target, operation, tmp_path):
    enroll(tmp_path)
    path = {"root": tmp_path, "directory": tmp_path / "recovery-registry",
            "file": tmp_path / "recovery-registry" / "personal.json",
            "lock": tmp_path / "recovery-registry" / "personal.lock",
            "temporary": tmp_path / "recovery-registry" / "personal.json.tmp",
            "installation": tmp_path / "installation.lock"}[target]
    if not path.exists():
        path.write_text("synthetic")
    path.chmod(0o755 if path.is_dir() else 0o644)
    original_mode = stat.S_IMODE(path.stat().st_mode)
    with pytest.raises(registry.RegistryError):
        if operation == "declare":
            registry.declare("personal", "key", "synthetic", "new", "1", root=tmp_path)
        elif operation == "has_pending":
            registry.has_pending("personal", "key", "synthetic", "new", "1", root=tmp_path)
        else:
            with registry.mutation("personal", "key", "synthetic", "new", "1", root=tmp_path):
                pytest.fail("unsafe path entered caller")
    assert stat.S_IMODE(path.stat().st_mode) == original_mode


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode fixture with synthetic reparse metadata")
def test_reparse_metadata_refused_without_permission_change(tmp_path, monkeypatch):
    enroll(tmp_path)
    path = tmp_path / "recovery-registry" / "personal.json"
    path.chmod(0o644)
    original_lstat = Path.lstat

    def reparse_lstat(candidate, *args, **kwargs):
        metadata = original_lstat(candidate, *args, **kwargs)
        if candidate == path:
            return SimpleNamespace(st_mode=metadata.st_mode, st_file_attributes=0x400,
                                   st_uid=metadata.st_uid, st_nlink=metadata.st_nlink)
        return metadata

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    with pytest.raises(registry.RegistryError, match="regular and unlinked"):
        registry.declare("personal", "key", "synthetic", "new", "1", root=tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


@pytest.mark.skipif(os.name == "nt", reason="POSIX creation modes")
@pytest.mark.parametrize("mask", [0o000, 0o022, 0o077])
def test_new_registry_paths_are_private_under_umask(tmp_path, mask):
    root = tmp_path / "parent" / "local"
    previous_mask = os.umask(mask)
    try:
        registry.declare("personal", "key", "synthetic", "account", "opaque/generation", root=root)
    finally:
        os.umask(previous_mask)
    for directory in (root.parent, root, root / "recovery-registry"):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for path in (root / "installation.lock", root / "recovery-registry" / "personal.lock",
                 root / "recovery-registry" / "personal.json"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor and directory creation modes")
def test_registry_metadata_creation_handles_restrictive_umask(tmp_path):
    root = tmp_path / "parent" / "local"
    local, path, lock = registry._paths("personal", root)
    previous_mask = os.umask(0o777)
    try:
        registry._paths_check(local, path, lock, create=True)
        with registry._record_lock(local, path, "personal", "synthetic", "account"):
            pass
    finally:
        os.umask(previous_mask)
    for directory in (root.parent, root, path.parent):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for candidate in (lock, root / "installation.lock", *path.parent.glob("record-*.lock")):
        assert stat.S_IMODE(candidate.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor permissions")
def test_new_lock_permission_failure_closes_descriptor(tmp_path, monkeypatch):
    descriptors = []

    def fail_permissions(descriptor, mode):
        descriptors.append(descriptor)
        raise OSError("synthetic chmod failure")

    monkeypatch.setattr(os, "fchmod", fail_permissions)
    with pytest.raises(registry.RegistryError):
        registry.declare("personal", "key", "synthetic", "account", "1", root=tmp_path)
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert not (tmp_path / "recovery-registry" / "personal.json").exists()


@pytest.mark.parametrize("mask", [0o000, 0o022, 0o077])
def test_logical_lock_names_are_private_profile_and_pair_hashes(tmp_path, mask):
    previous_mask = os.umask(mask)
    try:
        for profile in ("personal", "work"):
            with registry.mutation(profile, "key", "age-key", "artha", "1", root=tmp_path):
                pass
            with registry.mutation(profile, "key", "age-key", "artha", "2", root=tmp_path):
                pass
    finally:
        os.umask(previous_mask)
    locks = set((tmp_path / "recovery-registry").glob("record-*.lock"))
    expected = set()
    for profile in ("personal", "work"):
        identity = json.dumps([profile, "age-key", "artha"], separators=(",", ":")).encode("ascii")
        digest = hashlib.sha256(identity).hexdigest()
        expected.add(tmp_path / "recovery-registry" / f"record-{digest}.lock")
    assert locks == expected
    for lock in locks:
        assert "age-key" not in lock.name and "artha" not in lock.name
        if os.name != "nt":
            assert stat.S_IMODE(lock.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and link fixtures")
@pytest.mark.parametrize("unsafe", ["public", "symlink", "hardlink", "fifo"])
@pytest.mark.parametrize("operation", ["declare", "mutation"])
def test_unsafe_logical_lock_refused_before_metadata_or_native_write(tmp_path, unsafe, operation):
    before = enroll(tmp_path)
    lock = next((tmp_path / "recovery-registry").glob("record-*.lock"))
    if unsafe == "public":
        lock.chmod(0o644)
    else:
        lock.unlink()
        destination = tmp_path / "outside"
        destination.write_text("untouched")
        destination.chmod(0o600)
        if unsafe == "symlink":
            lock.symlink_to(destination)
        elif unsafe == "hardlink":
            os.link(destination, lock)
        else:
            os.mkfifo(lock, 0o600)
    with pytest.raises(registry.RegistryError):
        if operation == "declare":
            registry.declare("personal", "key", "synthetic", "version-1", "1", root=tmp_path)
        else:
            with registry.mutation("personal", "key", "synthetic", "version-1", "1", root=tmp_path):
                pytest.fail("unsafe logical lock reached native write")
    assert registry.snapshot("personal", root=tmp_path) == before
    if unsafe in ("symlink", "hardlink", "fifo"):
        assert destination.read_text() == "untouched"


def child(root, body):
    code = ("import sys; from pathlib import Path; "
            "from catchup_core import recovery_registry as r; "
            "from catchup_core.session_lock import installation_fence; "
            "root=Path(sys.argv[1]);\n" + body)
    return subprocess.Popen([sys.executable, "-u", "-c", code, str(root)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})


@pytest.mark.skipif(os.name == "nt", reason="real POSIX SIGKILL and shared flock")
@pytest.mark.parametrize("kind,generation", [("client", "1"), ("client", "2"), ("bundle", "1")])
def test_logical_record_process_race_and_death_retry(tmp_path, kind, generation):
    enroll(tmp_path)
    process = child(tmp_path,
                    "with r.mutation('personal','client','synthetic','same','1',root=root):\n"
                    " print('pending',flush=True)\n sys.stdin.read()\n"
                    " (root/'native-original').write_text('original')\n")
    try:
        assert process.stdout.readline().strip() == "pending", process.stderr.read()
        before = registry.snapshot("personal", root=tmp_path, require_review=False)
        contender = child(tmp_path,
                          f"with r.mutation('personal',{kind!r},'synthetic','same',{generation!r},root=root):\n"
                          " (root/'native-contender').write_text('stale')\n")
        _, errors = contender.communicate(timeout=10)
        assert contender.returncode != 0, errors
        assert not (tmp_path / "native-contender").exists()
        assert registry.snapshot("personal", root=tmp_path, require_review=False) == before
        peer = child(tmp_path,
                     "with r.mutation('personal','bundle','synthetic','other','1',root=root):\n"
                     " print('parallel',flush=True)\n")
        output, errors = peer.communicate(timeout=10)
        assert peer.returncode == 0, errors
        assert output.strip() == "parallel"
        os.kill(process.pid, signal.SIGKILL)
        process.communicate(timeout=10)
        assert not (tmp_path / "native-original").exists()
        assert registry.has_pending("personal", "client", "synthetic", "same", "1", root=tmp_path)
        retry = child(tmp_path,
                      "with r.mutation('personal','client','synthetic','same','1',root=root):\n"
                      " (root/'native-retry').write_text('verified retry')\n")
        _, errors = retry.communicate(timeout=10)
        assert retry.returncode == 0, errors
        assert (tmp_path / "native-retry").read_text() == "verified retry"
        assert not registry.has_pending("personal", "client", "synthetic", "same", "1", root=tmp_path)
        assert registry.snapshot("personal", root=tmp_path)["entries"][-2]["state"] == "active"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


@pytest.mark.skipif(os.name == "nt", reason="real POSIX SIGKILL and shared flock")
@pytest.mark.parametrize("published", [False, True])
def test_process_contention_capture_exclusion_and_sigkill(tmp_path, published):
    enroll(tmp_path)
    publication = " (root/'synthetic-publication').write_text('synthetic metadata')\n" if published else ""
    process = child(tmp_path,
                    "with r.mutation('personal','key','synthetic','killed','1',root=root):\n"
                    + publication +
                    " print('pending',flush=True)\n sys.stdin.read()\n")
    try:
        assert process.stdout.readline().strip() == "pending", process.stderr.read()
        with registry.mutation("personal", "client", "synthetic", "peer", "1", root=tmp_path):
            pass
        capture = child(tmp_path, "with installation_fence(root,exclusive=True):\n print('wrong')\n")
        _, errors = capture.communicate(timeout=10)
        assert capture.returncode != 0, errors
        os.kill(process.pid, signal.SIGKILL)
        process.communicate(timeout=10)
        document = registry.snapshot("personal", root=tmp_path, require_review=False)
        assert next(entry for entry in document["entries"] if entry["account"] == "killed")["state"] == "pending"
        assert (tmp_path / "synthetic-publication").exists() is published
        with pytest.raises(registry.RegistryError):
            registry.snapshot("personal", root=tmp_path)
        with registry.mutation("personal", "key", "synthetic", "killed", "1", root=tmp_path):
            assert registry.has_pending("personal", "key", "synthetic", "killed", "1", root=tmp_path)
        assert not registry.has_pending("personal", "key", "synthetic", "killed", "1", root=tmp_path)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="native Windows process termination and byte-range locks")
@pytest.mark.parametrize("published", [False, True], ids=["before-effect", "after-effect"])
def test_windows_registry_process_death_preserves_pending_intent_and_excludes_capture(tmp_path, published):
    enroll(tmp_path)
    effect = " (root/'synthetic-effect').write_text('synthetic metadata')\n" if published else ""
    process = child(tmp_path,
                    "with r.mutation('personal','client','synthetic','terminated','1',root=root):\n"
                    + effect
                    + " (root/'ready').write_text('ready')\n sys.stdin.read()\n")
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / "ready").exists():
            if process.poll() is not None:
                _, errors = process.communicate(timeout=10)
                pytest.fail(f"synthetic registry writer exited before readiness: {errors}")
            if time.monotonic() >= deadline:
                pytest.fail("synthetic registry writer did not become ready")
            time.sleep(0.01)
        pending = registry.snapshot("personal", root=tmp_path, require_review=False)
        with pytest.raises(registry.RegistryError):
            registry.snapshot("personal", root=tmp_path)
        contender = child(tmp_path,
                          "with r.mutation('personal','client','synthetic','terminated','1',root=root):\n"
                          " (root/'unexpected-effect').write_text('wrong')\n")
        _, errors = contender.communicate(timeout=15)
        assert contender.returncode != 0, errors
        assert not (tmp_path / "unexpected-effect").exists()
        capture = child(tmp_path, "with installation_fence(root,exclusive=True):\n print('wrong')\n")
        _, errors = capture.communicate(timeout=15)
        assert capture.returncode != 0, errors
        assert registry.snapshot("personal", root=tmp_path, require_review=False) == pending
        process.kill()
        process.communicate(timeout=15)
        assert (tmp_path / "synthetic-effect").exists() is published
        assert registry.has_pending("personal", "client", "synthetic", "terminated", "1", root=tmp_path)
        with pytest.raises(registry.RegistryError):
            registry.snapshot("personal", root=tmp_path)
        with registry.mutation("personal", "client", "synthetic", "terminated", "1", root=tmp_path):
            pass
        assert not registry.has_pending("personal", "client", "synthetic", "terminated", "1", root=tmp_path)
        assert next(entry for entry in registry.snapshot("personal", root=tmp_path)["entries"]
                    if entry["account"] == "terminated")["state"] == "active"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=15)


@pytest.mark.skipif(os.name != "nt", reason="native Windows registry DACL contract")
def test_windows_registry_creates_owner_private_directories_and_files(tmp_path):
    from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory, owner_private_handle_check

    root = tmp_path / "private-registry"
    enroll(root)
    with WindowsQuarantineDirectory.open(root) as local:
        assert owner_private_handle_check(local.handle, directory=True) == local.identity
        with local.open_file("installation.lock") as opened:
            assert owner_private_handle_check(opened.handle, directory=False) == opened.identity
        with local.open_directory("recovery-registry") as directory:
            assert owner_private_handle_check(directory.handle, directory=True) == directory.identity
            for name in directory.list_files():
                with directory.open_file(name) as opened:
                    assert owner_private_handle_check(opened.handle, directory=False) == opened.identity
            assert "personal.json" in directory.list_files()
            assert "personal.lock" in directory.list_files()
            assert any(name.startswith("record-") and name.endswith(".lock")
                       for name in directory.list_files())
    assert registry.snapshot("personal", root=root)["entries"]


@pytest.mark.skipif(os.name != "nt", reason="native Windows registry DACL contract")
@pytest.mark.parametrize("target", [
    "local", "directory", "registry", "temporary", "profile-lock", "installation-lock", "record-lock",
])
def test_windows_registry_rejects_existing_broad_dacl_without_repair(tmp_path, target):
    from catchup_core.quarantine_fs_windows import (
        QuarantineFilesystemError,
        WindowsQuarantineDirectory,
        owner_private_handle_check,
    )

    root = tmp_path / "private-registry"
    enroll(root)
    directory = root / "recovery-registry"
    record_lock = next(directory.glob("record-*.lock"))
    paths = {
        "local": root,
        "directory": directory,
        "registry": directory / "personal.json",
        "temporary": directory / "personal.json.tmp",
        "profile-lock": directory / "personal.lock",
        "installation-lock": root / "installation.lock",
        "record-lock": record_lock,
    }
    path = paths[target]
    if target == "temporary":
        with WindowsQuarantineDirectory.open(directory) as opened:
            with opened.create_file(path.name):
                pass
    contents = path.read_bytes() if path.is_file() else None
    set_everyone_readable_dacl(path)

    with pytest.raises(registry.RegistryError):
        registry.declare("personal", "key", "synthetic", "version-1", "1", root=root)
    if contents is not None:
        assert path.read_bytes() == contents
    if path.is_dir():
        with WindowsQuarantineDirectory.open(path, owner_private=False) as opened:
            with pytest.raises(QuarantineFilesystemError):
                owner_private_handle_check(opened.handle, directory=True)
    else:
        with WindowsQuarantineDirectory.open(path.parent, owner_private=False) as parent:
            with parent.open_file(path.name, writable=True, private=False) as opened:
                with pytest.raises(QuarantineFilesystemError):
                    owner_private_handle_check(opened.handle, directory=False)


@pytest.mark.parametrize("profile,kind,service,account,generation", [
    ("unknown", "key", "synthetic", "", "1"),
    ("personal", [], "synthetic", "", "1"),
    ("personal", "key", "", "", "1"),
    ("personal", "key", "synthetic", None, "1"),
    ("personal", "key", "synthetic", "", ""),
    ("personal", "key", "synthetic", "", "line\nbreak"),
])
def test_invalid_identity_does_not_create_root(tmp_path, profile, kind, service, account, generation):
    root = tmp_path / "absent"
    with pytest.raises(registry.RegistryError):
        registry.declare(profile, kind, service, account, generation, root=root)
    assert not root.exists()


def test_invalid_options_digest_and_review(tmp_path):
    with pytest.raises(registry.RegistryError):
        with registry.mutation("personal", "key", "synthetic", "", "1", root=tmp_path, deleted=1):
            pytest.fail("invalid deleted option entered caller")
    with pytest.raises(registry.RegistryError):
        registry.registry_digest([])
    with pytest.raises(registry.RegistryError):
        registry.attest_legacy("personal", "", "", reviewed_at(), root=tmp_path)
    with pytest.raises(registry.RegistryError):
        registry.attest_legacy("personal", "", "reviewer", None, root=tmp_path)
    with pytest.raises(registry.RegistryError):
        registry.snapshot("personal", root=tmp_path / ".." / "unsafe", require_review=False)


def test_lazy_import_and_canonical_digest(tmp_path):
    process = child(tmp_path, "assert 'catchup_core.crypto' not in sys.modules\nprint('clean')\n")
    output, errors = process.communicate(timeout=10)
    assert process.returncode == 0, errors
    assert output.strip() == "clean"
    document = enroll(tmp_path)
    assert registry.registry_digest(document) == registry.registry_digest(dict(reversed(list(document.items()))))


def test_private_files_and_atomic_failure_leave_pending(tmp_path, monkeypatch):
    enroll(tmp_path)
    if os.name == "nt":
        target, name = registry, "_windows_save"
    else:
        from catchup_core import backup

        target, name = backup, "atomic_bytes"
    original = getattr(target, name)
    calls = 0

    def fail_completion(path, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic unavailable filesystem")
        original(path, data)

    monkeypatch.setattr(target, name, fail_completion)
    with pytest.raises(registry.RegistryError):
        with registry.mutation("personal", "bundle", "synthetic", "failed-save", "1", root=tmp_path):
            pass
    document = registry.snapshot("personal", root=tmp_path, require_review=False)
    assert document["entries"][-1]["state"] == "pending"
    if os.name != "nt":
        assert (tmp_path / "recovery-registry" / "personal.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX filesystem and locking fixtures")
def test_hardlink_fifo_and_profile_lock_contention(tmp_path):
    enroll(tmp_path)
    from catchup_core.session_lock import session_lock

    lock = tmp_path / "recovery-registry" / "personal.lock"
    with session_lock(lock), pytest.raises(registry.RegistryError):
        registry.declare("personal", "key", "synthetic", "contender", "1", root=tmp_path)
    path = tmp_path / "recovery-registry" / "personal.json"
    os.link(path, tmp_path / "hardlinked")
    with pytest.raises(registry.RegistryError):
        registry.snapshot("personal", root=tmp_path, require_review=False)
    (tmp_path / "hardlinked").unlink()
    path.unlink()
    os.mkfifo(path, 0o600)
    with pytest.raises(registry.RegistryError):
        registry.snapshot("personal", root=tmp_path, require_review=False)


def test_bounded_entry_count_and_bad_json(tmp_path, monkeypatch):
    document = enroll(tmp_path)
    monkeypatch.setattr(registry, "_MAX_ENTRIES", 0)
    with pytest.raises(registry.RegistryError):
        registry.snapshot("personal", root=tmp_path, require_review=False)
    monkeypatch.setattr(registry, "_MAX_ENTRIES", 10000)
    path = write_document(tmp_path, document)
    for payload in (b"[", b"\xff", b"[]", b"[" * 2000):
        path.write_bytes(payload)
        with pytest.raises(registry.RegistryError):
            registry.snapshot("personal", root=tmp_path, require_review=False)


def test_encoded_size_bound_preserves_previous_revision(tmp_path, monkeypatch):
    document = enroll(tmp_path)
    monkeypatch.setattr(registry, "_MAX_BYTES", 1)
    with pytest.raises(registry.RegistryError):
        registry.registry_digest(document)
    monkeypatch.setattr(registry, "_MAX_BYTES", 4 * 1024 * 1024)
    assert registry.snapshot("personal", root=tmp_path) == document
