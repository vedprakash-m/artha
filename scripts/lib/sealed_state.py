"""Read and write encrypted State Files in memory, through ``scripts/vault.py read|write``.

Stdlib only.  Nothing here writes a plaintext copy of a sealed domain, and a failure raises instead of falling back
to a plaintext file (re-artha §10.6).  A domain with no sealed file is read as the plain ``<domain>.md`` it is.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_STATE_DIR = _SCRIPTS_DIR.parent / "state"


class SealedStateError(RuntimeError):
    pass


def _vault(verb: str, domain: str, stdin: str | None = None) -> str:
    if "/" in domain or domain.startswith("."):
        raise SealedStateError(f"invalid domain name {domain!r}")
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, str(_SCRIPTS_DIR / "vault.py"), verb, domain],
            input=stdin, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SealedStateError(f"vault {verb} of {domain} failed: {type(exc).__name__}") from exc
    if result.returncode != 0:
        raise SealedStateError(f"vault {verb} of {domain} failed (exit {result.returncode})")
    return result.stdout


def read_domain(domain: str, state_dir: Path | None = None) -> str:
    directory = state_dir or _STATE_DIR
    if (directory / f"{domain}.md.age").is_file():
        return _vault("read", domain)
    plain = directory / f"{domain}.md"
    if plain.is_file():
        return plain.read_text(encoding="utf-8")
    raise SealedStateError(f"no state file for {domain}")


def write_domain(domain: str, text: str, state_dir: Path | None = None) -> None:
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    from catchup_core.session_lock import writer_fence  # noqa: PLC0415 - refused during capture and when restored
    with writer_fence(state_dir or _STATE_DIR):
        _vault("write", domain, text)


def available(domain: str, state_dir: Path | None = None) -> bool:
    try:
        read_domain(domain, state_dir)
    except SealedStateError:
        return False
    return True
