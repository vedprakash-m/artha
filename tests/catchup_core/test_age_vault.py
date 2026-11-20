from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from catchup_core import vault as vt

def _find_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for candidate in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None

AGE = _find_bin("age")
KEYGEN = _find_bin("age-keygen")
needs_age = pytest.mark.skipif(not (AGE and KEYGEN), reason="age / age-keygen not installed")

DOC = "---\ndomain: kids\n---\n# Kids\n- OI-001 register\n- OI-002 forms\n"


@pytest.fixture
def keypair(tmp_path):
    out = subprocess.run([KEYGEN], capture_output=True, text=True, check=True)
    secret = [ln for ln in out.stdout.splitlines() if ln.startswith("AGE-SECRET-KEY-")][0]
    public = [ln for ln in out.stderr.splitlines() if "Public key" in ln][0].split(": ")[1].strip()
    return secret, public


@pytest.fixture
def vault(tmp_path, keypair):
    secret, public = keypair
    return vt.AgeVault(tmp_path / "state", identity=lambda: secret, recipient=public)


# ── structural validation ─────────────────────────────────────────────────

def test_validate_structure():
    assert vt.validate_structure(DOC, "kids").ok
    assert not vt.validate_structure("no frontmatter").ok
    assert "no 'domain'" in vt.validate_structure("---\nfoo: 1\n---\nx").problems[0]
    assert "expected" in vt.validate_structure(DOC, "finance").problems[0]
    assert not vt.validate_structure(DOC + "\x00").ok
    crlf = "---\r\ndomain: kids\r\n---\r\n# Kids\r\n- OI-001\r\n"
    assert vt.validate_structure(crlf, "kids").ok
    eof_delimiter = "---\ndomain: kids\n---"
    assert vt.validate_structure(eof_delimiter, "kids").ok
    duplicate = "---\ndomain: kids\ndomain: finance\n---\nbody\n"
    report = vt.validate_structure(duplicate, "kids")
    assert not report.ok and any("duplicate" in problem for problem in report.problems)


def test_record_ids():
    assert vt.record_ids("OI-1 and DEC-004, G-12; SIG-9 CON-3 SPR-2 SCN-7 OIX-1") == {
        "OI-1", "DEC-004", "G-12", "SIG-9", "CON-3", "SPR-2", "SCN-7"}


def test_check_write_blocks_silent_record_loss_but_not_edits():
    smaller = "---\ndomain: kids\n---\n# Kids\n- OI-001 register\n"
    with pytest.raises(vt.DestructiveWrite, match="OI-002"):
        vt.check_write(DOC, smaller)
    vt.check_write(DOC, smaller, approved_removals=["OI-002"])
    longer = DOC.replace("register", "register — updated text with more detail") + "- OI-003 new\n"
    vt.check_write(DOC, longer)                                   # edits and additions are fine
    vt.check_write(None, DOC)
    with pytest.raises(vt.DestructiveWrite, match="domain"):
        vt.check_write(DOC, DOC.replace("domain: kids", "domain: finance"))
    with pytest.raises(vt.DestructiveWrite):
        vt.check_write(DOC, "garbage")


def test_empty_template_is_not_flagged_as_destructive():
    """The old size-shrink check left empty template files locked."""
    template = "---\ndomain: business\n---\n# Business\n"
    vt.check_write(None, template, expected_domain="business")
    vt.check_write(template, template)


def test_split_monthly_keeps_continuations_and_preamble():
    text = "# Audit\n[2026-09-30T01:00:00Z] A\n  detail\n[2026-10-01T02:00:00Z] B\n[2026-10-02T03:00:00Z] C\n"
    seg = vt.split_monthly(text)
    assert list(seg) == ["2026-09", "2026-10"]
    assert "# Audit" in seg["2026-09"] and "detail" in seg["2026-09"]
    assert seg["2026-10"].count("\n") == 2
    assert vt.split_monthly("") == {}


# ── age round trip ────────────────────────────────────────────────────────

@needs_age
def test_roundtrip_in_memory_and_ciphertext_on_disk(vault, tmp_path):
    path = vault.write("kids", DOC)
    assert path.name == "kids.md.age" and vault.exists("kids")
    assert b"OI-001" not in path.read_bytes()
    assert vault.read("kids") == DOC
    leftovers = [p.name for p in (tmp_path / "state").iterdir()]
    assert leftovers == ["kids.md.age"], "no plaintext or temp file may remain"


@needs_age
def test_write_keeps_recoverable_versions_and_validates(vault):
    vault.write("kids", DOC)
    v2 = DOC + "- OI-003 third\n"
    vault.write("kids", v2)
    assert len(vault.versions("kids")) == 1
    vault.write("kids", v2 + "- OI-004\n")
    vault.write("kids", v2 + "- OI-004\n- OI-005\n")
    vault.write("kids", v2 + "- OI-004\n- OI-005\n- OI-006\n")
    assert len(vault.versions("kids")) == 3                       # capped
    with pytest.raises(vt.DestructiveWrite):
        vault.write("kids", "---\ndomain: kids\n---\n# emptied\n")
    assert "OI-003" in vault.read("kids")                          # untouched


@needs_age
def test_approved_deletion_allowed(vault):
    vault.write("kids", DOC)
    vault.write("kids", "---\ndomain: kids\n---\n# Kids\n- OI-001 register\n", approved_removals=["OI-002"])
    assert "OI-002" not in vault.read("kids")


@needs_age
def test_wrong_key_and_missing_file_and_bad_names(tmp_path, keypair, vault):
    vault.write("kids", DOC)
    other = subprocess.run([KEYGEN], capture_output=True, text=True, check=True).stdout
    wrong = [ln for ln in other.splitlines() if ln.startswith("AGE-SECRET-KEY-")][0]
    bad = vt.AgeVault(tmp_path / "state", identity=lambda: wrong, recipient=keypair[1])
    with pytest.raises(vt.VaultError, match="decryption failed"):
        bad.read("kids")
    with pytest.raises(vt.VaultError, match="no encrypted file"):
        vault.read("nothing")
    with pytest.raises(vt.VaultError):
        vault.path_for("../escape")
    with pytest.raises(vt.VaultError):
        vault.path_for(".hidden")


@needs_age
def test_key_failures_never_degrade_to_plaintext(tmp_path, keypair):
    secret, public = keypair
    v = vt.AgeVault(tmp_path / "state", identity=lambda: secret, recipient=public)
    v.write("kids", DOC)

    def boom():
        raise RuntimeError("keychain locked")
    with pytest.raises(vt.VaultUnavailable):
        vt.AgeVault(tmp_path / "state", identity=boom, recipient=public).read("kids")
    with pytest.raises(vt.VaultUnavailable):
        vt.AgeVault(tmp_path / "state", identity=lambda: "", recipient=public).read("kids")


def test_missing_age_binary_fails_closed(tmp_path):
    v = vt.AgeVault(tmp_path, identity=lambda: "x", recipient="age1x", age_bin="")
    v._age = None
    with pytest.raises(vt.VaultUnavailable):
        v.write("kids", DOC)
    (tmp_path / "kids.md.age").write_bytes(b"x")
    with pytest.raises(vt.VaultUnavailable):
        v.read("kids")


@pytest.mark.parametrize("kind", ["hardlink", "directory"])
def test_ciphertext_path_link_or_nonfile_is_refused(tmp_path, kind):
    v = vt.AgeVault(
        tmp_path,
        identity=lambda: "unused",
        recipient="age1unused",
        age_bin="/bin/true",
    )
    path = tmp_path / "kids.md.age"
    if kind == "hardlink":
        original = tmp_path / "original"
        original.write_bytes(b"unchanged")
        path.hardlink_to(original)
    else:
        path.mkdir()
    with pytest.raises(vt.VaultError, match="ordinary unlinked"):
        v.read("kids")
    if kind == "hardlink":
        assert original.read_bytes() == b"unchanged"
    else:
        assert path.is_dir()


def test_original_ciphertext_revision_rejects_stale_write(monkeypatch, tmp_path):
    v = vt.AgeVault(
        tmp_path,
        identity=lambda: "unused",
        recipient="age1unused",
        age_bin="/bin/true",
    )
    path = tmp_path / "kids.md.age"
    path.write_bytes(b"first")
    _, revision = v.revision_for("kids")
    path.write_bytes(b"second")
    with pytest.raises(vt.VaultConflict, match="changed"):
        v.write(
            "kids",
            DOC,
            expected_revision=revision,
            previous=DOC,
        )
    assert path.read_bytes() == b"second"


def test_explicit_create_revision_refuses_existing_ciphertext(tmp_path):
    v = vt.AgeVault(
        tmp_path,
        identity=lambda: "unused",
        recipient="age1unused",
        age_bin="/bin/true",
    )
    path = tmp_path / "kids.md.age"
    path.write_bytes(b"existing")
    with pytest.raises(vt.VaultConflict, match="created"):
        v.write("kids", DOC, expected_revision="", previous=DOC)
    assert path.read_bytes() == b"existing"


def test_revision_token_is_bound_to_target(tmp_path):
    v = vt.AgeVault(
        tmp_path,
        identity=lambda: "unused",
        recipient="age1unused",
        age_bin="/bin/true",
    )
    (tmp_path / "kids.md.age").write_bytes(b"same")
    (tmp_path / "goals.md.age").write_bytes(b"same")
    _, kids_revision = v.revision_for("kids")
    _, goals_revision = v.revision_for("goals")
    assert kids_revision != goals_revision


def test_conflict_scan_unknown_maps_before_and_after_publication(monkeypatch, tmp_path):
    from lib.sync_conflicts import ConflictScanUnknown

    vault = vt.AgeVault(
        tmp_path,
        identity=lambda: "unused",
        recipient="age1unused",
    )
    path = tmp_path / "kids.md.age"
    monkeypatch.setattr(
        vt,
        "find_conflicts",
        lambda _path: (_ for _ in ()).throw(ConflictScanUnknown("synthetic unknown")),
    )
    with pytest.raises(vt.VaultConflict, match="unknown"):
        vault._check_conflicts(path, "kids")
    with pytest.raises(vt.VaultUncertain, match="unknown"):
        vault._check_conflicts(path, "kids", published=True)


def test_visible_conflict_maps_before_and_after_publication(monkeypatch, tmp_path):
    vault = vt.AgeVault(
        tmp_path,
        identity=lambda: "unused",
        recipient="age1unused",
    )
    path = tmp_path / "kids.md.age"
    conflict = tmp_path / "kids-HOST.md.age"
    monkeypatch.setattr(vt, "find_conflicts", lambda _path: [conflict])
    with pytest.raises(vt.VaultConflict, match="copy present"):
        vault._check_conflicts(path, "kids")
    with pytest.raises(vt.VaultUncertain, match="publication completed"):
        vault._check_conflicts(path, "kids", published=True)


def test_invalid_revision_and_rotation_failure_are_refused(monkeypatch, tmp_path):
    vault = vt.AgeVault(
        tmp_path,
        identity=lambda: "unused",
        recipient="age1unused",
        age_bin="/bin/true",
    )
    (tmp_path / "kids.md.age").write_bytes(b"original")
    monkeypatch.setattr(vault, "_encrypt", lambda _text: b"new")
    with pytest.raises(vt.VaultConflict, match="invalid"):
        vault._write("kids", DOC, "kids", (), DOC, "not-a-revision")

    monkeypatch.setattr(vault, "_rotate_versions", lambda _path: (_ for _ in ()).throw(
        OSError("synthetic rotate failure")
    ))
    with pytest.raises(OSError, match="rotate failure"):
        vault._write("kids", DOC, "kids", (), DOC, None)


def test_refusal_marker_failure_does_not_replace_vault_failure(tmp_path, monkeypatch):
    vault = vt.AgeVault(
        tmp_path / "state",
        identity=lambda: "unused",
        recipient="age1unused",
    )
    repository = Path(vt.__file__).resolve().parents[2]
    target = repository / "state" / "synthetic.md.age"
    monkeypatch.setattr(
        vt,
        "record_refusal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic marker")),
    )
    vault._record_state_refusal(target, "refused")
    vault._record_state_refusal(tmp_path / "outside.md.age", "refused")

@pytest.mark.parametrize("name", ["", r"..\escape", r"folder\finance", r"C:\finance", "finance:stream"])
def test_vault_rejects_nonportable_state_names_before_io(tmp_path, name):
    vault = vt.AgeVault(tmp_path, identity=lambda: "unused", recipient="age1unused")
    with pytest.raises(vt.VaultError, match="invalid vault name"):
        vault.path_for(name)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("path_uses_isfile", [False, True])
def test_windows_portable_age_installation_is_discovered(monkeypatch, tmp_path, path_uses_isfile):
    monkeypatch.setattr(vt.shutil, "which", lambda _: None)
    if path_uses_isfile:
        monkeypatch.setattr(vt.Path, "is_file", lambda path: vt.os.path.isfile(path))
    monkeypatch.setattr(vt, "sys", type("Platform", (), {"platform": "win32"}), raising=False)
    monkeypatch.setattr(vt.Path, "home", lambda: tmp_path)
    executable = tmp_path / ".artha-tools" / "age" / "age.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic executable path, never executed")
    assert vt.find_age_bin() == str(executable)


@needs_age
def test_curated_encrypted_read_uses_selected_directory_and_no_plaintext(monkeypatch, tmp_path, keypair):
    import foundation
    from catchup_core.curated import read_state

    secret, public = keypair
    configured, selected = tmp_path / "configured", tmp_path / "selected"
    monkeypatch.setitem(foundation._config, "STATE_DIR", configured)
    monkeypatch.setattr(foundation, "get_public_key", lambda: public)
    monkeypatch.setattr(foundation, "get_private_key", lambda: secret)
    vt.AgeVault(configured, identity=lambda: secret, recipient=public).write("kids", DOC)
    expected = DOC + "- OI-003 selected directory\n"
    vt.AgeVault(selected, identity=lambda: secret, recipient=public).write("kids", expected)

    assert read_state(selected, "kids") == expected
    assert read_state(configured, "kids") == DOC
    assert sorted(path.name for path in selected.iterdir()) == ["kids.md.age"]
    assert sorted(path.name for path in configured.iterdir()) == ["kids.md.age"]


def test_portable_identity_uses_stdin_without_a_key_file(monkeypatch, tmp_path):
    from types import SimpleNamespace
    v = vt.AgeVault(tmp_path, identity=lambda: "x", recipient="age1x", age_bin="/bin/true")
    (tmp_path / "kids.md.age").write_bytes(b"encrypted")
    seen = {}
    def decrypt(argv, **kw):
        seen.update(kw)
        assert argv[1:4] == ["-d", "-i", "-"]
        return SimpleNamespace(returncode=0, stdout=DOC.encode())
    monkeypatch.setattr(vt.subprocess, "run", decrypt)
    assert v.read("kids") == DOC and seen["input"] == b"x\n"
    assert "pass_fds" not in seen


@needs_age
def test_encryption_failure_writes_nothing(tmp_path, keypair):
    v = vt.AgeVault(tmp_path / "state", identity=lambda: keypair[0], recipient="not-a-recipient")
    with pytest.raises(vt.VaultError):
        v.write("kids", DOC)
    assert not (tmp_path / "state").exists() or list((tmp_path / "state").iterdir()) == []


@needs_age
def test_no_plaintext_or_key_left_in_tmpdir_after_read(tmp_path, keypair):
    secret, public = keypair
    state = tmp_path / "state"
    vt.AgeVault(state, identity=lambda: secret, recipient=public).write("kids", DOC)
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    code = (
        "import sys; sys.path.insert(0, %r); from catchup_core.vault import AgeVault; "
        "v = AgeVault(%r, identity=lambda: %r, recipient=%r); print(len(v.read('kids')))"
        % (str(__import__('pathlib').Path(vt.__file__).parent.parent), str(state), secret, public)
    )
    env = {**os.environ, "TMPDIR": str(tmpdir)}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert list(tmpdir.iterdir()) == []
    blob = b"".join(p.read_bytes() for p in state.rglob("*") if p.is_file())
    assert secret.encode() not in blob and b"OI-001" not in blob


def test_ciphertext_changed_between_staging_and_replacement_is_refused(vault, tmp_path, monkeypatch):
    vault.write("goals", "---\ndomain: goals\n---\nold\n")
    target = vault.path_for("goals")
    before = target.read_bytes()
    real = vt.AgeVault._cipher_revision
    calls = {"n": 0}

    def drifting(path):
        calls["n"] += 1
        # the second look (after staging) sees a different revision: another writer replaced the file
        return "0" * 64 if calls["n"] == 2 else real(path)

    monkeypatch.setattr(vt.AgeVault, "_cipher_revision", staticmethod(drifting))
    with pytest.raises(vt.VaultConflict, match="changed before replacement"):
        vault.write("goals", "---\ndomain: goals\n---\nnew\n")
    assert target.read_bytes() == before


def test_publication_readback_mismatch_and_post_replace_failure_are_uncertain(vault, monkeypatch):
    vault.write("goals", "---\ndomain: goals\n---\nold\n")
    real_read = Path.read_bytes

    def corrupt_readback(self):
        data = real_read(self)
        return data + b"x" if self.name == "goals.md.age" else data

    monkeypatch.setattr(Path, "read_bytes", corrupt_readback)
    with pytest.raises(vt.VaultUncertain, match="verification failed"):
        vault.write("goals", "---\ndomain: goals\n---\nnew\n")
    monkeypatch.undo()

    syncs = {"n": 0}

    def fail_dir_sync(path):
        syncs["n"] += 1
        if syncs["n"] >= 2:  # the first call belongs to version rotation; the second follows publication
            raise OSError("synthetic directory sync failure")

    monkeypatch.setattr(vt.AgeVault, "_fsync_directory", staticmethod(fail_dir_sync))
    with pytest.raises(vt.VaultUncertain, match="outcome is uncertain"):
        vault.write("goals", "---\ndomain: goals\n---\nnewer\n")


def test_directory_sync_is_skipped_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    vt.AgeVault._fsync_directory(tmp_path / "does-not-exist")
