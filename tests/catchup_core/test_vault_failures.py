"""AgeVault fails closed on every subprocess and filesystem failure (re-artha §10.6)."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from catchup_core import vault as vt


def make(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "kids.md.age").write_bytes(b"cipher")
    return vt.AgeVault(state, identity=lambda: "AGE-SECRET-KEY-1X", recipient="age1x", age_bin="/fake/age")


def test_find_age_bin_prefers_custom_then_path_then_known_locations(monkeypatch):
    assert vt.find_age_bin("/custom/age") == "/custom/age"
    monkeypatch.setattr(vt.shutil, "which", lambda _n: "/usr/bin/age")
    assert vt.find_age_bin() == "/usr/bin/age"
    monkeypatch.setattr(vt.shutil, "which", lambda _n: None)
    monkeypatch.setattr(vt.os.path, "isfile", lambda p: p == "/usr/local/bin/age")
    monkeypatch.setattr(vt.os, "access", lambda _p, _m: True)
    assert vt.find_age_bin() == "/usr/local/bin/age"
    monkeypatch.setattr(vt.os.path, "isfile", lambda _p: False)
    assert vt.find_age_bin() is None


def test_age_that_cannot_run_is_unavailable_not_plaintext(tmp_path, monkeypatch):
    v = make(tmp_path)
    def boom(*_a, **_k):
        raise OSError("exec failed")
    monkeypatch.setattr(vt.subprocess, "run", boom)
    with pytest.raises(vt.VaultUnavailable):
        v.read("kids")
    with pytest.raises(vt.VaultUnavailable):
        v.write("kids", "---\ndomain: kids\n---\nx\n", previous="")
    monkeypatch.setattr(vt.subprocess, "run", lambda *_a, **_k: (_ for _ in ()).throw(subprocess.TimeoutExpired("age", 1)))
    with pytest.raises(vt.VaultUnavailable):
        v.read("kids")


def test_non_utf8_plaintext_is_an_error(tmp_path, monkeypatch):
    v = make(tmp_path)
    monkeypatch.setattr(vt.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=b"\xff\xfe", stderr=b""))
    with pytest.raises(vt.VaultError, match="UTF-8"):
        v.read("kids")


def test_failed_replace_leaves_no_temp_file_and_keeps_the_old_ciphertext(tmp_path, monkeypatch):
    v = make(tmp_path)
    monkeypatch.setattr(vt.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=b"newcipher", stderr=b""))
    def refuse(_src, _dst):
        raise OSError("disk full")
    monkeypatch.setattr(vt.os, "replace", refuse)
    with pytest.raises(OSError):
        v.write("kids", "---\ndomain: kids\n---\nx\n", previous="")
    leftovers = [p.name for p in (tmp_path / "state").iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [] and (tmp_path / "state" / "kids.md.age").read_bytes() == b"cipher"


def test_structure_parser_ignores_indented_dash_and_comment_lines():
    fields = vt._frontmatter("---\nkey: v\n  nested: x\n- item: y\n# c: z\nother: 'q'\n---\n")
    assert fields == {"key": "v", "other": "q"}
    assert vt._frontmatter("no front matter") is None
