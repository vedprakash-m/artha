"""In-memory vault access (re-artha §10.6, WP-06).

The legacy ``vault.py decrypt`` writes plaintext State Files next to their
ciphertext and relies on an exit-time re-encrypt that cannot work across
separate agent tool calls.  This module decrypts **into process memory**:

* the private key comes from a callable (the OS credential store in production)
  and reaches ``age`` through an inherited pipe — never a file;
* ciphertext is read from its ``.age`` path, plaintext returns on a pipe;
* writes encrypt from memory, replace atomically, and keep recoverable versions;
* a write is validated structurally (domain, record identity, approved
  deletions) instead of by a file-size shrink heuristic.

A missing key, binary or platform support fails closed (:class:`VaultUnavailable`);
there is no plaintext fallback.
"""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "AgeVault", "DestructiveWrite", "StructureReport", "VaultError", "VaultUnavailable", "check_write",
    "find_age_bin", "record_ids", "split_monthly", "validate_structure",
]

_RECORD_ID = re.compile(r"\b(?:OI|DEC|SCN|SIG|G|SPR|CON)-\d+\b")
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_KEEP_VERSIONS = 3


class VaultError(RuntimeError):
    pass


class VaultUnavailable(VaultError):
    """Key, binary or platform support is missing.  Callers block; no plaintext fallback."""


class DestructiveWrite(VaultError):
    """A write would remove records or change identity without approval."""


# ── structural validation (replaces the size-shrink heuristic) ─────────────

@dataclass(frozen=True)
class StructureReport:
    ok: bool
    problems: tuple[str, ...]
    domain: str | None


def _frontmatter(text: str) -> dict[str, str] | None:
    m = _FRONTMATTER.match(text)
    if not m:
        return None
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "-", "#")):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip("\"'")
    return fields


def validate_structure(text: str, expected_domain: str | None = None) -> StructureReport:
    problems: list[str] = []
    fm = _frontmatter(text)
    domain: str | None = None
    if fm is None:
        problems.append("missing YAML frontmatter")
    else:
        domain = fm.get("domain")
        if not domain:
            problems.append("frontmatter has no 'domain'")
        elif expected_domain and domain != expected_domain:
            problems.append(f"domain is {domain!r}, expected {expected_domain!r}")
    if "\x00" in text:
        problems.append("contains NUL bytes")
    return StructureReport(not problems, tuple(problems), domain)


def record_ids(text: str) -> set[str]:
    """Stable identifiers present in a State File (Open Items, Decisions, Goals, …)."""
    return set(_RECORD_ID.findall(text))


def check_write(old: str | None, new: str, *, expected_domain: str | None = None,
                approved_removals: Iterable[str] = ()) -> None:
    """Raise :class:`DestructiveWrite` unless ``new`` is a structurally valid successor of ``old``."""
    report = validate_structure(new, expected_domain)
    if not report.ok:
        raise DestructiveWrite("; ".join(report.problems))
    if old is None:
        return
    old_fm, new_fm = _frontmatter(old) or {}, _frontmatter(new) or {}
    if old_fm.get("domain") and old_fm.get("domain") != new_fm.get("domain"):
        raise DestructiveWrite("write changes the file's domain")
    missing = record_ids(old) - record_ids(new) - set(approved_removals)
    if missing:
        raise DestructiveWrite(f"write drops {len(missing)} record(s) without approval: {sorted(missing)[:5]}")


def split_monthly(audit_text: str) -> dict[str, str]:
    """Split an append-only audit log into ``YYYY-MM`` segments (continuations follow their entry)."""
    stamp = re.compile(r"^\[(\d{4}-\d{2})-\d{2}")
    segments: dict[str, list[str]] = defaultdict(list)
    current: str | None = None
    preamble: list[str] = []
    for line in audit_text.splitlines():
        m = stamp.match(line)
        if m:
            current = m.group(1)
        if current is None:
            preamble.append(line)
        else:
            segments[current].append(line)
    if preamble and segments:
        first = min(segments)
        segments[first] = preamble + segments[first]
    return {month: "\n".join(lines) + "\n" for month, lines in sorted(segments.items())}


# ── age access ────────────────────────────────────────────────────────────────
def find_age_bin(custom: str | None = None) -> str | None:
    """Locate the age binary, checking PATH and common installation directories."""
    if custom:
        return custom
    found = shutil.which("age")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/age", "/usr/local/bin/age"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


class AgeVault:
    def __init__(
        self,
        state_dir: Path | str,
        *,
        identity: Callable[[], str],
        recipient: str,
        age_bin: str | None = None,
    ):
        self._dir = Path(state_dir)
        self._identity = identity
        self._recipient = recipient
        self._age = find_age_bin(age_bin)

    def _require(self) -> str:
        if not self._age:
            raise VaultUnavailable("the age binary is not installed")
        return self._age

    def path_for(self, name: str) -> Path:
        if "/" in name or name.startswith("."):
            raise VaultError(f"invalid vault name {name!r}")
        return self._dir / f"{name}.md.age"

    def exists(self, name: str) -> bool:
        return self.path_for(name).is_file()

    # decrypt ---------------------------------------------------------------
    def read(self, name: str) -> str:
        age = self._require()
        path = self.path_for(name)
        if not path.is_file():
            raise VaultError(f"{name}: no encrypted file")
        try:
            identity = self._identity()
        except Exception as exc:
            raise VaultUnavailable(f"private key unavailable: {type(exc).__name__}") from exc
        if not identity:
            raise VaultUnavailable("private key is empty")
        try:
            proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                [age, "-d", "-i", "-", str(path)], input=(identity.rstrip() + "\n").encode(),
                capture_output=True, timeout=60, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise VaultUnavailable(f"age failed to run: {type(exc).__name__}") from exc
        if proc.returncode != 0:
            raise VaultError(f"{name}: decryption failed")
        try:
            return proc.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise VaultError(f"{name}: plaintext is not valid UTF-8") from exc

    # encrypt ---------------------------------------------------------------
    def _encrypt(self, text: str) -> bytes:
        age = self._require()
        try:
            proc = subprocess.run(  # noqa: S603
                [age, "-r", self._recipient], input=text.encode("utf-8"),
                capture_output=True, timeout=60, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise VaultUnavailable(f"age failed to run: {type(exc).__name__}") from exc
        if proc.returncode != 0 or not proc.stdout:
            raise VaultError("encryption failed; nothing written")
        return proc.stdout

    def write(self, name: str, text: str, *, expected_domain: str | None = None,
              approved_removals: Iterable[str] = (), previous: str | None = None) -> Path:
        """Validate, encrypt from memory, keep a recoverable version, replace atomically."""
        path = self.path_for(name)
        if previous is None and path.is_file():
            previous = self.read(name)
        check_write(previous, text, expected_domain=expected_domain or name,
                    approved_removals=approved_removals)
        blob = self._encrypt(text)
        self._dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._dir, prefix=f".{name}-", suffix=".age.tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            if path.is_file():
                self._rotate_versions(path)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        return path

    def _rotate_versions(self, path: Path) -> None:
        for n in range(_KEEP_VERSIONS - 1, 0, -1):
            src = path.with_name(f"{path.name}.v{n}")
            if src.is_file():
                os.replace(src, path.with_name(f"{path.name}.v{n + 1}"))
        shutil.copy2(path, path.with_name(f"{path.name}.v1"))
        extra = path.with_name(f"{path.name}.v{_KEEP_VERSIONS + 1}")
        with contextlib.suppress(OSError):
            extra.unlink()

    def versions(self, name: str) -> list[Path]:
        base = self.path_for(name)
        return [p for n in range(1, _KEEP_VERSIONS + 1)
                if (p := base.with_name(f"{base.name}.v{n}")).is_file()]
