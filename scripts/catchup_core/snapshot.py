"""Consistent snapshots with a generation manifest (re-artha §7.6, §7.9).

A snapshot is one generation across the ledger database, selected curated files
and key *metadata* (versions only — never key material).  The database copy uses
SQLite's online backup API so it is consistent while the ledger is open.  The
manifest is written last and atomically, so a manifest that exists always refers
to a complete snapshot.

Restore verifies every hash before touching the target and never restores over a
newer sealed deletion checkpoint without leaving the ledger locked (the ledger's
own :meth:`check_restore` enforces that on open).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["SnapshotError", "SnapshotManifest", "restore_snapshot", "take_snapshot", "verify_snapshot"]


class SnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class SnapshotManifest:
    generation: int
    taken_at: str
    ledger_file: str
    ledger_sha256: str
    schema_version: int
    curated: Mapping[str, str]            # relative name -> sha256
    key_metadata: Mapping[str, Any]
    sealed_checkpoint_id: int

    def to_json(self) -> str:
        return json.dumps({
            "generation": self.generation, "taken_at": self.taken_at, "ledger_file": self.ledger_file,
            "ledger_sha256": self.ledger_sha256, "schema_version": self.schema_version,
            "curated": dict(self.curated), "key_metadata": dict(self.key_metadata),
            "sealed_checkpoint_id": self.sealed_checkpoint_id,
        }, sort_keys=True, indent=2)

    @classmethod
    def from_json(cls, text: str) -> SnapshotManifest:
        try:
            d = json.loads(text)
            return cls(int(d["generation"]), str(d["taken_at"]), str(d["ledger_file"]), str(d["ledger_sha256"]),
                       int(d["schema_version"]), dict(d["curated"]), dict(d["key_metadata"]),
                       int(d["sealed_checkpoint_id"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise SnapshotError("manifest is malformed") from exc


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def take_snapshot(
    ledger_path: Path,
    dest_dir: Path,
    *,
    generation: int,
    curated_files: Mapping[str, Path] | None = None,
    key_metadata: Mapping[str, Any] | None = None,
    sealed_checkpoint_id: int = 0,
    now: datetime | None = None,
) -> Path:
    """Write ``ledger-<gen>.db``, copies of curated files and ``manifest-<gen>.json``."""
    if generation < 1:
        raise SnapshotError("generation must be >= 1")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest_path = dest_dir / f"manifest-{generation}.json"
    if manifest_path.exists():
        raise SnapshotError(f"generation {generation} already exists; snapshots are immutable")

    db_copy = dest_dir / f"ledger-{generation}.db"
    src = sqlite3.connect(str(ledger_path))
    try:
        dst = sqlite3.connect(str(db_copy))
        try:
            src.backup(dst)
            ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
            version = int(dst.execute("PRAGMA user_version").fetchone()[0])
        finally:
            dst.close()
    finally:
        src.close()
    if ok != "ok":
        db_copy.unlink(missing_ok=True)
        raise SnapshotError("snapshot failed its integrity check")

    curated: dict[str, str] = {}
    for name, source in (curated_files or {}).items():
        if "/" in name or name.startswith("."):
            raise SnapshotError(f"curated file name {name!r} must be a plain file name")
        target = dest_dir / f"curated-{generation}-{name}"
        shutil.copy2(source, target)
        curated[name] = _sha256(target)

    manifest = SnapshotManifest(
        generation=generation,
        taken_at=(now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ledger_file=db_copy.name, ledger_sha256=_sha256(db_copy), schema_version=version,
        curated=curated, key_metadata=dict(key_metadata or {}), sealed_checkpoint_id=sealed_checkpoint_id,
    )
    _atomic_write(manifest_path, manifest.to_json())      # last: its existence means "complete"
    return manifest_path


def verify_snapshot(manifest_path: Path) -> SnapshotManifest:
    manifest_path = Path(manifest_path)
    try:
        manifest = SnapshotManifest.from_json(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SnapshotError(f"cannot read manifest: {exc}") from exc
    folder = manifest_path.parent
    ledger = folder / manifest.ledger_file
    if not ledger.is_file() or _sha256(ledger) != manifest.ledger_sha256:
        raise SnapshotError("ledger snapshot missing or its hash does not match the manifest")
    for name, digest in manifest.curated.items():
        copy = folder / f"curated-{manifest.generation}-{name}"
        if not copy.is_file() or _sha256(copy) != digest:
            raise SnapshotError(f"curated file {name} missing or modified")
    return manifest


def restore_snapshot(manifest_path: Path, target_ledger: Path, *, curated_targets: Mapping[str, Path] | None = None
                     ) -> SnapshotManifest:
    """Verify, then copy the ledger (and curated files) into place.  Nothing is touched on failure."""
    manifest = verify_snapshot(manifest_path)
    folder = Path(manifest_path).parent
    target_ledger = Path(target_ledger)
    target_ledger.parent.mkdir(parents=True, exist_ok=True)
    staged = target_ledger.with_suffix(target_ledger.suffix + ".restoring")
    shutil.copy2(folder / manifest.ledger_file, staged)
    for suffix in ("-wal", "-shm"):
        Path(str(target_ledger) + suffix).unlink(missing_ok=True)      # stale WAL must not outlive the restore
    os.replace(staged, target_ledger)
    for name, target in (curated_targets or {}).items():
        if name in manifest.curated:
            shutil.copy2(folder / f"curated-{manifest.generation}-{name}", target)
    return manifest
