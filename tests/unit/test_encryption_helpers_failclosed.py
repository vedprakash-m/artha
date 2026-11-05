"""WP-06 / §10.6: string helpers never fall back to plaintext or write key/plaintext files."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import action_queue as aq  # noqa: E402
import foundation  # noqa: E402

needs_age = pytest.mark.skipif(not (shutil.which("age") and shutil.which("age-keygen")),
                               reason="age not installed")


@pytest.fixture
def keypair():
    out = subprocess.run(["age-keygen"], capture_output=True, text=True, check=True)
    secret = next(ln for ln in out.stdout.splitlines() if ln.startswith("AGE-SECRET-KEY-"))
    public = next(ln for ln in out.stderr.splitlines() if "Public key" in ln).split(": ")[1].strip()
    return secret, public


def test_encrypt_field_without_recipient_raises_instead_of_returning_plaintext():
    with pytest.raises(aq.ActionEncryptionError):
        aq._encrypt_field("secret", None)


def test_encrypt_field_helper_failure_raises(monkeypatch):
    def boom(pub, val):
        raise RuntimeError("age missing")
    monkeypatch.setattr(foundation, "age_encrypt_string", boom)
    with pytest.raises(aq.ActionEncryptionError):
        aq._encrypt_field("secret", "age1abc")


@needs_age
def test_string_roundtrip_leaves_no_temp_files(keypair, tmp_path, monkeypatch):
    secret, public = keypair
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmpdir))
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmpdir))
    token = foundation.age_encrypt_string(public, "hello restricted world")
    assert "hello" not in token
    assert foundation.age_decrypt_string(secret, token) == "hello restricted world"
    assert list(tmpdir.iterdir()) == []
    assert aq._decrypt_field(aq._encrypt_field("payload", public), secret) == "payload"


@needs_age
def test_wrong_key_raises(keypair):
    other = subprocess.run(["age-keygen"], capture_output=True, text=True, check=True).stdout
    wrong = next(ln for ln in other.splitlines() if ln.startswith("AGE-SECRET-KEY-"))
    token = foundation.age_encrypt_string(keypair[1], "x")
    with pytest.raises(RuntimeError):
        foundation.age_decrypt_string(wrong, token)


def test_missing_age_binary_raises(monkeypatch):
    from unittest.mock import Mock

    monkeypatch.setattr(foundation, "_find_age_bin", lambda: None)
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="not found"):
        foundation.age_encrypt_string("age1x", "v")
    with pytest.raises(RuntimeError, match="not found"):
        foundation.age_decrypt_string("AGE-SECRET-KEY-1X", "AAAA")
    run.assert_not_called()


def test_string_encryption_uses_shared_executable_discovery(monkeypatch):
    from types import SimpleNamespace

    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"synthetic ciphertext")

    monkeypatch.setattr(foundation, "_find_age_bin", lambda: "portable-age")
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(subprocess, "run", run)

    assert foundation.age_encrypt_string("age1synthetic", "synthetic input")
    assert calls[0][0] == ["portable-age", "-r", "age1synthetic"]
    assert calls[0][1]["input"] == b"synthetic input"


@needs_age
def test_windows_string_decryption_never_writes_private_key_or_plaintext(keypair, tmp_path, monkeypatch):
    import base64
    import tempfile
    from types import SimpleNamespace

    secret, public = keypair
    plaintext = "synthetic protected action description"
    token = foundation.age_encrypt_string(public, plaintext)
    ciphertext = base64.b64decode(token)
    original_run = subprocess.run
    calls = []

    def inspect_decrypt(argv, **kwargs):
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].read_bytes() == ciphertext
        assert argv[1:4] == ["-d", "-i", "-"]
        assert kwargs["input"] == (secret + "\n").encode()
        calls.append(argv)
        return original_run(argv, **kwargs)

    monkeypatch.setattr(foundation, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", inspect_decrypt)

    assert foundation.age_decrypt_string(secret, token) == plaintext
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_windows_string_decryption_failure_cleans_ciphertext(monkeypatch, tmp_path):
    import tempfile
    from types import SimpleNamespace

    def fail(argv, **kwargs):
        assert len(list(tmp_path.iterdir())) == 1
        assert argv[1:4] == ["-d", "-i", "-"]
        return SimpleNamespace(returncode=1, stdout=b"")

    monkeypatch.setattr(foundation, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(foundation, "_find_age_bin", lambda: "synthetic-age")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fail)

    with pytest.raises(RuntimeError, match="decryption failed"):
        foundation.age_decrypt_string("synthetic private key", "AAAA")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("returncode,expected", [(0, True), (1, False)])
def test_file_decryption_never_materializes_the_private_key(monkeypatch, tmp_path, returncode, expected):
    import tempfile
    from types import SimpleNamespace

    encrypted = tmp_path / "source.age"
    encrypted.write_bytes(b"synthetic ciphertext")
    destination = tmp_path / "output.md"

    def run(argv, **kwargs):
        assert sorted(path.name for path in tmp_path.iterdir()) == ["source.age"]
        assert argv == [
            "synthetic-age", "--decrypt", "--identity", "-",
            "--output", str(destination), str(encrypted),
        ]
        assert kwargs["input"] == "synthetic private key\n"
        assert kwargs["timeout"] == 60
        assert "synthetic private key" not in repr(argv)
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(foundation, "_find_age_bin", lambda: "synthetic-age")
    monkeypatch.setattr(subprocess, "run", run)
    assert foundation.age_decrypt("synthetic private key\n\n", encrypted, destination) is expected
    assert sorted(path.name for path in tmp_path.iterdir()) == ["source.age"]


@needs_age
def test_native_file_decryption_uses_stdin_identity(keypair, tmp_path, monkeypatch):
    import tempfile

    secret, public = keypair
    encrypted = tmp_path / "source.age"
    output = tmp_path / "output.bin"
    plaintext = b"\x00synthetic binary recovery\xff"
    encrypted.write_bytes(subprocess.run(
        ["age", "-r", public], input=plaintext, capture_output=True, check=True,
    ).stdout)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    assert foundation.age_decrypt(secret, encrypted, output) is True
    assert output.read_bytes() == plaintext
    assert sorted(path.name for path in tmp_path.iterdir()) == ["output.bin", "source.age"]
