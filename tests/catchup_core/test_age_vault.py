from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from catchup_core import vault as vt

AGE = shutil.which("age")
KEYGEN = shutil.which("age-keygen")
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


def test_windows_is_explicitly_unsupported(monkeypatch, tmp_path):
    v = vt.AgeVault(tmp_path, identity=lambda: "x", recipient="age1x", age_bin="/bin/true")
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(vt.VaultUnavailable, match="Windows"):
        v.read("kids")


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
