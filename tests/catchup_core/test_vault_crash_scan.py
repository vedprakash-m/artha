"""Crash-injected plaintext scan (re-artha §10.6): a process killed at any point after it decrypted a State File, and
every reader that goes through the in-memory vault, leave no plaintext of that file anywhere in the workspace."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from catchup_core.vault import AgeVault

AGE, KEYGEN = shutil.which("age"), shutil.which("age-keygen")
pytestmark = pytest.mark.skipif(not (AGE and KEYGEN), reason="age is not installed")
MARK = "SYNTHETIC-CANARY-7f3a91c2e8"
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture
def sealed(tmp_path):
    key = tmp_path / "key.txt"
    out = subprocess.run([KEYGEN, "-o", str(key)], capture_output=True, text=True, check=True)
    recipient = (out.stderr + out.stdout).split("Public key:")[1].split()[0]
    identity = "\n".join(line for line in key.read_text().splitlines() if line.startswith("AGE-SECRET-KEY-"))
    key.unlink()                                       # the identity lives only in memory from here on
    state = tmp_path / "state"
    AgeVault(state, identity=lambda: identity, recipient=recipient).write(
        "finance", f"---\ndomain: finance\n---\n{MARK}\n")
    return state, identity, recipient, tmp_path


def _leaks(root: Path) -> list[str]:
    found = []
    for path in root.rglob("*"):
        if path.is_file() and MARK.encode() in path.read_bytes():
            found.append(str(path.relative_to(root)))
    return found


def test_a_process_killed_right_after_decrypting_leaves_no_plaintext(sealed):
    state, identity, recipient, root = sealed
    child = textwrap.dedent(f"""
        import os, signal, sys
        sys.path.insert(0, {str(SCRIPTS)!r})
        from catchup_core.vault import AgeVault
        text = AgeVault({str(state)!r}, identity=lambda: {identity!r}, recipient={recipient!r}).read("finance")
        assert {MARK!r} in text
        os.kill(os.getpid(), signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
    """)
    proc = subprocess.run([sys.executable, "-c", child], capture_output=True, env={**os.environ, "TMPDIR": str(root)})
    assert proc.returncode in ((15,) if os.name == "nt" else (-9, 137)), proc.stderr.decode(errors="replace")
    assert [p for p in _leaks(root) if not p.endswith(".age")] == []


def test_a_process_killed_in_the_middle_of_a_write_leaves_no_plaintext_and_no_torn_file(sealed):
    state, identity, recipient, root = sealed
    child = textwrap.dedent(f"""
        import os, signal, sys
        sys.path.insert(0, {str(SCRIPTS)!r})
        from catchup_core import vault
        v = vault.AgeVault({str(state)!r}, identity=lambda: {identity!r}, recipient={recipient!r})
        real = os.replace
        def die(*a, **k):
            os.kill(os.getpid(), signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
        vault.os.replace = die
        v.write("finance", "---\\ndomain: finance\\n---\\n{MARK} changed\\n")
    """)
    proc = subprocess.run([sys.executable, "-c", child], capture_output=True, env={**os.environ, "TMPDIR": str(root)})
    assert proc.returncode in ((15,) if os.name == "nt" else (-9, 137)), proc.stderr.decode(errors="replace")
    assert _leaks(root) == []                              # even the stray temp file is ciphertext
    survivor = AgeVault(state, identity=lambda: identity, recipient=recipient).read("finance")
    assert MARK in survivor and "changed" not in survivor      # the previous version is intact


def test_vault_cli_read_and_every_reader_leave_the_workspace_clean(sealed, monkeypatch):
    state, identity, recipient, root = sealed
    from lib import sealed_state  # noqa: PLC0415
    monkeypatch.setenv("ARTHA_AGE_KEY", identity)
    monkeypatch.setattr(sealed_state, "_vault", lambda verb, domain, stdin=None: AgeVault(
        state, identity=lambda: identity, recipient=recipient).read(domain))
    assert MARK in sealed_state.read_domain("finance", state)
    assert [p for p in _leaks(root) if not p.endswith(".age")] == []
