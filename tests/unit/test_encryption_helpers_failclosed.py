"""WP-06 / §10.6: the legacy encryption helpers no longer fall back to plaintext or temp files."""
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
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError):
        foundation.age_encrypt_string("age1x", "v")
    with pytest.raises(RuntimeError):
        foundation.age_decrypt_string("AGE-SECRET-KEY-1X", "AAAA")
