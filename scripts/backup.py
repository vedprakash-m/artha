#!/usr/bin/env python3
"""
backup.py — Artha GFS backup/restore manager
==============================================
Standalone CLI for archive management: snapshot, status, validate, restore,
install, and key disaster recovery (export-key / import-key).

Architecture:
  - All functions read paths from foundation._config at call time.
  - Backup-specific constants (BACKUP_DIR, BACKUP_MANIFEST) are also stored
    in _config so a single fixture patch covers all modules in tests.
  - Module-level BACKUP_DIR / BACKUP_MANIFEST aliases are frozen at import
    time and exist only for __all__ / external read access.
    NEVER use them inside function bodies — use _config["BACKUP_DIR"] instead.

GFS tier retention:
  daily   → 7 ZIPs     weekly → 4 ZIPs
  monthly → 12 ZIPs    yearly → unlimited

ZIP layout per snapshot:
  backups/{tier}/YYYY-MM-DD.zip
    manifest.json                     <- internal metadata + SHA-256 per file
    state/finance.md.age              <- state_encrypted: copied as-is
    state/goals.md.age                <- state_plain: encrypted on-the-fly
    config/user_profile.yaml.age      <- config: encrypted on-the-fly

Ref: TS §8.5.2, specs/bkp-rst.md
"""

from __future__ import annotations

# Auto-relaunch inside the Artha venv if not already running there
import sys, os as _os
_scripts_dir = _os.path.dirname(_os.path.abspath(__file__))
_artha_dir   = _os.path.dirname(_scripts_dir)  # project root (parent of scripts/)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)
if _artha_dir not in sys.path:
    sys.path.insert(0, _artha_dir)
from _bootstrap import reexec_in_venv
reexec_in_venv()

import argparse
import ctypes
import hashlib
import json
import os
import re
import sqlite3
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from datetime import date as _date_type, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, NoReturn

try:
    import yaml as _yaml  # PyYAML — in requirements.txt
except ImportError:  # pragma: no cover
    _yaml = None  # type: ignore[assignment]

from foundation import (
    _config,
    SENSITIVE_FILES,
    _normalize_sensitive_files,
    log, die,
    get_public_key, get_private_key,
    check_age_installed, age_encrypt, age_decrypt,
    is_valid_age_file, _find_age_bin,
    KC_SERVICE, KC_ACCOUNT,
)

# ---------------------------------------------------------------------------
# Backup-specific config entries (extend foundation._config)
# ---------------------------------------------------------------------------

if "BACKUP_DIR" not in _config:
    _config["BACKUP_DIR"]      = _config["ARTHA_DIR"] / "backups"
    _config["BACKUP_MANIFEST"] = _config["BACKUP_DIR"] / "manifest.json"

# Module-level aliases (frozen at import time — for __all__ and external read access ONLY)
BACKUP_DIR      = _config["BACKUP_DIR"]
BACKUP_MANIFEST = _config["BACKUP_MANIFEST"]

GFS_RETENTION: dict[str, int | None] = {
    "daily": 7, "weekly": 4, "monthly": 12, "yearly": None,
}
_PUBLICATION_THREAD_LOCK = threading.RLock()
_SOURCE_TYPES = {"state_encrypted", "state_plain", "config", "local_db"}
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


# ---------------------------------------------------------------------------
# Backup registry loader (moved from vault.py; now public API)
# ---------------------------------------------------------------------------

def load_backup_registry() -> list:
    """Return the authoritative list of files to include in each backup snapshot.

    Reads config/user_profile.yaml → backup section. Falls back to
    SENSITIVE_FILES (all state_encrypted) when the section is absent or
    unreadable so that existing behaviour is preserved.

    Each entry is a dict with:
      name         — unique slug used as the manifest 'domain' field
      source_type  — 'state_encrypted' | 'state_plain' | 'config' | 'local_db'
      source_path  — absolute Path to the live file
      restore_path — path relative to ARTHA_DIR for restoration
    """
    state_dir  = _config["STATE_DIR"]
    config_dir = _config["CONFIG_DIR"]
    artha_dir  = _config["ARTHA_DIR"]

    backup_cfg = None
    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        profile = load_config("user_profile")
        backup_cfg = profile.get("backup")
    except Exception:
        backup_cfg = None

    entries: list = []

    if backup_cfg:
        for sf in backup_cfg.get("state_files", []):
            name      = sf["name"]
            sensitive = sf.get("sensitive", False)
            if sensitive:
                entries.append({
                    "name":         name,
                    "source_type":  "state_encrypted",
                    "source_path":  state_dir / f"{name}.md.age",
                    "restore_path": f"state/{name}.md.age",
                })
            else:
                entries.append({
                    "name":         name,
                    "source_type":  "state_plain",
                    "source_path":  state_dir / f"{name}.md",
                    "restore_path": f"state/{name}.md",
                })
        for cfg_rel in backup_cfg.get("config_files", []):
            slug = "cfg__" + cfg_rel.replace("/", "__").replace(".", "_")
            entries.append({
                "name":         slug,
                "source_type":  "config",
                "source_path":  artha_dir / cfg_rel,
                "restore_path": cfg_rel,
            })

    if not entries:
        # Fallback: SENSITIVE_FILES list — all state_encrypted
        for domain, ext in _normalize_sensitive_files(_config["SENSITIVE_FILES"]):
            entries.append({
                "name":         domain,
                "source_type":  "state_encrypted",
                "source_path":  state_dir / f"{domain}{ext}.age",
                "restore_path": f"state/{domain}{ext}.age",
            })

    # Always include agent-learned procedures (AR-5, agentic-reloaded.md).
    # These are non-sensitive markdown files created by the agent from experience.
    proc_dir = artha_dir / "state" / "learned_procedures"
    if proc_dir.is_dir():
        for proc_file in sorted(proc_dir.glob("*.md")):
            if proc_file.name.lower() == "readme.md":
                continue  # README is tracked by git, not backup
            slug = "proc__" + proc_file.stem
            entries.append({
                "name":         slug,
                "source_type":  "state_plain",
                "source_path":  proc_file,
                "restore_path": f"state/learned_procedures/{proc_file.name}",
            })

    # artha-local-dbs (R8): SQLite databases in ~/.artha-local/ are excluded from
    # OneDrive sync to prevent WAL/SHM corruption, but must still be covered by GFS
    # backup snapshots.  Each *.db file is encrypted on-the-fly (same as state_plain).
    artha_local_dir = _config["ARTHA_LOCAL_DIR"]
    if artha_local_dir.is_dir():
        for db_file in sorted(artha_local_dir.glob("*.db")):
            slug = "localdb__" + db_file.stem
            entries.append({
                "name":         slug,
                "source_type":  "local_db",
                "source_path":  db_file,
                "restore_path": f".artha-local/{db_file.name}",
            })

    # §13.2 JSON state files (non-sensitive, not covered by state_files md list)
    _JSON_STATE_FILES = [
        "state/token_usage.json",
    ]
    for rel in _JSON_STATE_FILES:
        src = artha_dir / rel
        if src.exists():
            slug = "json__" + src.stem
            entries.append({
                "name": slug,
                "source_type": "state_plain",
                "source_path": src,
                "restore_path": rel,
            })

    return entries


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _file_sha256(path: Path) -> str:
    """Return streaming SHA-256 hex digest of *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@contextmanager
def _publication_lock() -> Iterator[None]:
    """Serialize archive/catalog publication across threads and processes."""
    with _PUBLICATION_THREAD_LOCK:
        lock_path = _config["BACKUP_DIR"] / ".publication.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as lock_file:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                if not lock_file.read(1):
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                unlock = lambda: msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                unlock = lambda: fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            try:
                yield
            finally:
                unlock()


def _save_manifest_unlocked(manifest: dict) -> None:
    manifest_path = _config["BACKUP_MANIFEST"]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(manifest_path) + ".tmp")
    try:
        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(manifest_path))
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        log(f"MANIFEST_SAVE_FAILED | error: {exc}")
        raise RuntimeError("Backup catalog publication failed.") from exc


def _safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        return False
    parts = value.split("/")
    return (
        not value.startswith("/")
        and all(part not in ("", ".", "..") and ":" not in part for part in parts)
        and Path(value).as_posix() == value
    )


def _safe_stream_name(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value not in (".", "..")
        and not any(char in value for char in ':/\\\0<>\"|?*')
        and not any(ord(char) < 32 for char in value)
    )


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if path.is_symlink():
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _safe_destination(root: Path, restore_path: str) -> Path:
    if not _safe_relative_path(restore_path):
        raise ValueError(f"unsafe restore path: {restore_path!r}")
    base = root.resolve()
    destination = root.joinpath(*restore_path.split("/"))
    if not destination.resolve(strict=False).is_relative_to(base):
        raise ValueError(f"restore path escapes root: {restore_path!r}")
    current = root
    for part in restore_path.split("/"):
        current = current / part
        if _is_link_or_reparse(current):
            raise ValueError(f"linked restore destination: {restore_path!r}")
    return destination


def _age_encrypt_bytes(recipient: str, plaintext: bytes) -> bytes:
    age_cmd = _find_age_bin()
    if not age_cmd:
        raise RuntimeError("age CLI not found; cannot encrypt payload")
    result = subprocess.run(
        [age_cmd, "--recipient", recipient],
        input=plaintext,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError("age payload encryption failed")
    return result.stdout


def _age_decrypt_bytes(identity: str, ciphertext: bytes) -> bytes:
    age_cmd = _find_age_bin()
    if not age_cmd:
        raise RuntimeError("age CLI not found; cannot decrypt payload")
    fd, ciphertext_path = tempfile.mkstemp(prefix="artha_age_payload_", suffix=".age")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(ciphertext)
        result = subprocess.run(
            [age_cmd, "--decrypt", "--identity", "-", ciphertext_path],
            input=(identity.rstrip() + "\n").encode("utf-8"),
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("age payload decryption failed")
        return result.stdout
    finally:
        Path(ciphertext_path).unlink(missing_ok=True)


def _sqlite_online_snapshot(source_path: Path) -> bytes:
    """Serialize a consistent read-only SQLite snapshot, including committed WAL data."""
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    source = target = None
    try:
        source_uri = source_path.resolve().as_uri() + "?mode=ro"
        source = sqlite3.connect(source_uri, uri=True, timeout=30)
        source.execute("PRAGMA query_only=ON")
        target = sqlite3.connect(":memory:")
        source.backup(target)
        image = target.serialize()
        if not image.startswith(b"SQLite format 3\0"):
            raise ValueError("online backup did not produce a SQLite image")
        if image[18:20] == b"\x02\x02":
            image = image[:18] + b"\x01\x01" + image[20:]
        return image
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()


def _validate_sqlite_image(content: bytes) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(content)
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("SQLite integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("SQLite foreign key check failed")
    except sqlite3.Error as exc:
        raise ValueError(f"invalid SQLite image: {exc}") from exc
    finally:
        connection.close()


def _supports_named_streams() -> bool:
    return os.name == "nt"


def _read_named_streams(source_path: Path) -> dict[str, bytes]:
    if not _supports_named_streams():
        return {}
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class StreamData(ctypes.Structure):
        _fields_ = [("size", ctypes.c_longlong), ("name", ctypes.c_wchar * 296)]

    find_first = kernel32.FindFirstStreamW
    find_first.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(StreamData), ctypes.c_uint]
    find_first.restype = ctypes.c_void_p
    find_next = kernel32.FindNextStreamW
    find_next.argtypes = [ctypes.c_void_p, ctypes.POINTER(StreamData)]
    find_next.restype = ctypes.c_int
    close_handle = kernel32.FindClose
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    data = StreamData()
    handle = find_first(str(source_path), 0, ctypes.byref(data), 0)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error == 38:
            return {}
        raise OSError(error, "Unable to enumerate NTFS streams", str(source_path))

    streams = {}
    try:
        while True:
            stream_name = data.name
            if stream_name != "::$DATA":
                match = re.fullmatch(r":(.+):\$DATA", stream_name)
                if not match or not _safe_stream_name(match.group(1)):
                    raise ValueError(f"unsupported NTFS stream name: {stream_name!r}")
                name = match.group(1)
                with open(f"{source_path}:{name}", "rb") as stream:
                    streams[name] = stream.read()
            if not find_next(handle, ctypes.byref(data)):
                error = ctypes.get_last_error()
                if error != 38:
                    raise OSError(error, "Unable to enumerate NTFS streams", str(source_path))
                break
    finally:
        close_handle(handle)
    return streams


def _write_named_stream(destination: Path, name: str, content: bytes) -> None:
    if not _supports_named_streams():
        raise OSError("native named-stream support is unavailable")
    if not _safe_stream_name(name):
        raise ValueError(f"unsafe NTFS stream name: {name!r}")
    with open(f"{destination}:{name}", "wb") as stream:
        stream.write(content)


def _unique_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _descriptor(value: Any, *, version: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("invalid payload descriptor")
    entry = value.get("entry")
    if version == "3":
        if not isinstance(entry, str) or not re.fullmatch(r"payload/[A-Za-z0-9_-]+\.age", entry):
            raise ValueError("unsafe version-3 payload entry")
    elif not _safe_relative_path(entry):
        raise ValueError("unsafe version-2 archive entry")
    digest = value.get("sha256")
    size = value.get("size")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ValueError("invalid payload checksum")
    if type(size) is not int or size < 0:
        raise ValueError("invalid payload size")
    return {"entry": entry, "sha256": digest, "size": size}


def _archive_records(
    zf: zipfile.ZipFile,
    internal: Any,
    expected_catalog: dict | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(internal, dict):
        raise ValueError("invalid backup manifest")
    version = internal.get("artha_backup_version")
    if version not in ("2", "3"):
        raise ValueError(f"unsupported backup version: {version!r}")
    if (
        not isinstance(internal.get("created"), str)
        or not internal["created"]
        or not isinstance(internal.get("date"), str)
        or not isinstance(internal.get("tier"), str)
        or internal["tier"] not in GFS_RETENTION
    ):
        raise ValueError("invalid backup metadata")
    try:
        _date_type.fromisoformat(internal["date"])
    except ValueError as exc:
        raise ValueError("invalid backup date") from exc
    files = internal.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("empty or invalid file manifest")

    records: list[dict[str, Any]] = []
    archive_entries: list[str] = []
    restore_paths: set[str] = set()
    stream_count = 0
    for key, meta in files.items():
        if not isinstance(meta, dict):
            raise ValueError("invalid file record")
        source_type = meta.get("source_type")
        name = meta.get("name")
        restore_path = meta.get("restore_path")
        if source_type not in _SOURCE_TYPES or not isinstance(name, str) or not name:
            raise ValueError("invalid source type or file name")
        if not _safe_relative_path(restore_path) or restore_path in restore_paths:
            raise ValueError("unsafe or duplicate restore path")
        if (
            (source_type in ("state_encrypted", "state_plain") and not restore_path.startswith("state/"))
            or (source_type == "state_encrypted" and not restore_path.endswith(".age"))
            or (source_type == "config" and not restore_path.startswith("config/"))
            or (source_type == "local_db"
                and (not restore_path.startswith(".artha-local/")
                     or Path(restore_path).suffix.lower() not in (".db", ".sqlite", ".sqlite3")))
        ):
            raise ValueError("restore path does not match source type")
        restore_paths.add(restore_path)

        if version == "2":
            descriptor = _descriptor({
                "entry": key,
                "sha256": meta.get("sha256"),
                "size": meta.get("size"),
            }, version=version)
            expected_entry = restore_path if source_type == "state_encrypted" else restore_path + ".age"
            if descriptor["entry"] != expected_entry:
                raise ValueError("version-2 payload path does not match restore path")
            streams = {}
        else:
            if key != restore_path:
                raise ValueError("version-3 file key does not match restore path")
            descriptor = _descriptor(meta.get("data"), version=version)
            streams_value = meta.get("streams")
            if not isinstance(streams_value, dict):
                raise ValueError("invalid stream manifest")
            streams = {}
            for stream_name, stream_descriptor in streams_value.items():
                if not _safe_stream_name(stream_name):
                    raise ValueError("unsafe named-stream name")
                if stream_name in streams:
                    raise ValueError("duplicate named-stream name")
                streams[stream_name] = _descriptor(stream_descriptor, version=version)
            stream_count += len(streams)

        archive_entries.append(descriptor["entry"])
        archive_entries.extend(stream["entry"] for stream in streams.values())
        records.append({
            "restore_path": restore_path,
            "source_type": source_type,
            "name": name,
            "data": descriptor,
            "streams": streams,
        })

    names = [info.filename for info in zf.infolist()]
    if any(info.is_dir() for info in zf.infolist()):
        raise ValueError("unexpected archive directory entry")
    if len(names) != len(set(names)) or len(archive_entries) != len(set(archive_entries)):
        raise ValueError("duplicate archive entry")
    if set(names) != set(archive_entries) | {"manifest.json"}:
        raise ValueError("MANIFEST COVERAGE FAIL: archive inventory mismatch")
    if expected_catalog is not None:
        file_count = expected_catalog.get("file_count")
        if type(file_count) is not int or file_count != len(records):
            raise ValueError("catalog file count mismatch")
        if expected_catalog.get("date") != internal["date"] or expected_catalog.get("tier") != internal["tier"]:
            raise ValueError("catalog metadata mismatch")
        catalog_stream_count = expected_catalog.get("stream_count", 0)
        if type(catalog_stream_count) is not int or catalog_stream_count != stream_count:
            raise ValueError("catalog stream count mismatch")
    return version, records


def _validate_content(source_type: str, restore_path: str, content: bytes) -> None:
    logical_path = restore_path[:-4] if source_type == "state_encrypted" and restore_path.endswith(".age") else restore_path
    suffix = Path(logical_path).suffix.lower()
    if source_type == "local_db":
        _validate_sqlite_image(content)
        return
    if suffix == ".md" and source_type in ("state_plain", "state_encrypted"):
        if not content:
            raise ValueError("EMPTY markdown payload")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("markdown payload is not UTF-8") from exc
        if not text.lstrip().startswith("---"):
            raise ValueError("YAML frontmatter missing")
        if len(text.split()) < 30:
            raise ValueError("SHORT markdown payload")
        return
    if suffix == ".json":
        try:
            json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON payload") from exc
    elif suffix in (".yaml", ".yml"):
        if _yaml is None:
            raise ValueError("YAML validation is unavailable")
        try:
            _yaml.safe_load(content.decode("utf-8"))
        except (UnicodeDecodeError, _yaml.YAMLError) as exc:
            raise ValueError("invalid YAML payload") from exc
    elif not content:
        raise ValueError("empty payload")


def _verify_payloads(
    zf: zipfile.ZipFile,
    records: list[dict[str, Any]],
    identity: str,
) -> list[dict[str, Any]]:
    verified = []
    for record in records:
        payload = record["data"]
        ciphertext = zf.read(payload["entry"])
        if len(ciphertext) != payload["size"] or hashlib.sha256(ciphertext).hexdigest() != payload["sha256"]:
            raise ValueError(f"CHECKSUM or SIZE FAIL: {payload['entry']}")
        try:
            plaintext = _age_decrypt_bytes(identity, ciphertext)
        except RuntimeError as exc:
            raise ValueError(f"DECRYPT FAIL: {payload['entry']}") from exc
        _validate_content(record["source_type"], record["restore_path"], plaintext)
        stream_values = {}
        for stream_name, descriptor in record["streams"].items():
            stream_ciphertext = zf.read(descriptor["entry"])
            if (len(stream_ciphertext) != descriptor["size"]
                    or hashlib.sha256(stream_ciphertext).hexdigest() != descriptor["sha256"]):
                raise ValueError(f"STREAM CHECKSUM or SIZE FAIL: {descriptor['entry']}")
            try:
                stream_values[stream_name] = _age_decrypt_bytes(identity, stream_ciphertext)
            except RuntimeError as exc:
                raise ValueError(f"STREAM DECRYPT FAIL: {descriptor['entry']}") from exc
        verified.append({
            **record,
            "ciphertext": ciphertext,
            "plaintext": plaintext,
            "stream_values": stream_values,
        })
    return verified


def _load_manifest() -> dict:
    """Load the catalog; only a genuinely missing file starts an empty history."""
    manifest_path = _config["BACKUP_MANIFEST"]
    try:
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_pairs,
        )
    except FileNotFoundError:
        return {"last_validate": None, "snapshots": {}}
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        log(f"MANIFEST_READ_FAILED | error_type: {type(exc).__name__}")
        raise RuntimeError("Backup catalog could not be read; existing file left unchanged.") from exc
    if (
        not isinstance(manifest, dict)
        or not isinstance(manifest.get("snapshots", {}), dict)
        or any(not isinstance(meta, dict) for meta in manifest.get("snapshots", {}).values())
    ):
        log("MANIFEST_READ_FAILED | reason: invalid_catalog_structure")
        raise RuntimeError("Backup catalog structure is invalid; existing file left unchanged.")
    return manifest


def _save_manifest(manifest: dict) -> None:
    """Atomically overwrite manifest.json via a .tmp sibling under the publication lock."""
    with _publication_lock():
        _save_manifest_unlocked(manifest)


def _get_backup_tier(d: "_date_type") -> str:
    """Return the GFS tier for date *d*.

    Priority (highest first):
      yearly  — December 31
      monthly — last day of any month (next calendar day is a different month)
      weekly  — Sunday
      daily   — everything else
    """
    if d.month == 12 and d.day == 31:
        return "yearly"
    if (d + timedelta(days=1)).month != d.month:
        return "monthly"
    if d.weekday() == 6:  # Sunday == 6
        return "weekly"
    return "daily"


def _prune_backups(tier: str, keep_n: int) -> None:
    """Apply retention while serializing catalog and archive updates."""
    with _publication_lock():
        _prune_backups_locked(tier, keep_n)


def _prune_backups_locked(tier: str, keep_n: int) -> None:
    backup_dir = _config["BACKUP_DIR"]
    tier_dir   = backup_dir / tier
    if not tier_dir.exists():
        return
    zips = sorted(tier_dir.glob("*.zip"), key=lambda p: p.stem)  # stem = YYYY-MM-DD
    if len(zips) <= keep_n:
        return
    manifest = _load_manifest()
    snapshots = manifest.get("snapshots", {})

    # Build a global domain→set-of-checksums map from ALL retained snapshots
    # (i.e. snapshots that are NOT candidates for pruning in this call)
    candidates = {f"{tier}/{z.name}" for z in zips[: len(zips) - keep_n]}
    removable = []

    for old_zip in zips[: len(zips) - keep_n]:
        key = f"{tier}/{old_zip.name}"
        snap_meta = snapshots.get(key, {})
        domain_checksums = snap_meta.get("domain_checksums", {})

        # Check if any domain checksum in this ZIP is unique across all other snapshots
        pinned = False
        if domain_checksums:
            for domain, chk in domain_checksums.items():
                found_elsewhere = False
                for other_key, other_meta in snapshots.items():
                    if other_key == key:
                        continue
                    if other_key in candidates and other_key != key:
                        continue  # skip other candidates (they might also be pruned)
                    other_checksums = other_meta.get("domain_checksums", {})
                    if other_checksums.get(domain) == chk:
                        found_elsewhere = True
                        break
                if not found_elsewhere:
                    log(f"BACKUP_PIN | zip: {old_zip.name} | reason: sole_carrier_of_{domain} | sha256: {chk[:16]}")
                    pinned = True
                    break

        if pinned:
            candidates.discard(key)
            continue
        removable.append(old_zip)

    if not removable:
        return
    for old_zip in removable:
        snapshots.pop(f"{tier}/{old_zip.name}", None)
    _save_manifest_unlocked(manifest)
    for old_zip in removable:
        try:
            old_zip.unlink()
            log(f"BACKUP_PRUNED | zip: {old_zip.name} | tier: {tier} | retention: {keep_n}")
        except OSError as exc:
            log(f"BACKUP_PRUNE_FAILED | zip: {old_zip.name} | error: {exc}")


def _zip_archive_path(entry: dict) -> str:
    """Return the path of this entry's file inside the backup ZIP archive.

    state_encrypted: restore_path is already '.age' — use as-is.
    state_plain / config / local_db: restore_path ends without '.age' —
        append '.age' because we encrypt on-the-fly before storing.
    """
    if entry["source_type"] == "state_encrypted":
        return entry["restore_path"]      # e.g. 'state/finance.md.age'
    return entry["restore_path"] + ".age" # e.g. 'state/goals.md.age' | '.artha-local/cashflow.db.age'


# ---------------------------------------------------------------------------
# Core backup operations
# ---------------------------------------------------------------------------

def backup_snapshot(registry: list, today: "_date_type | None" = None) -> int:
    """Create one ZIP snapshot containing all registered files for today's GFS tier.

    *registry* is the list returned by load_backup_registry() (passed as a
    parameter for testability — callers supply it).

    Called automatically at the end of a successful vault.py encrypt cycle, and
    available as the ``backup.py snapshot`` CLI command.

    ZIP layout:
      backups/{tier}/YYYY-MM-DD.zip
        manifest.json           <- internal metadata + SHA-256 per archived file
        state/finance.md.age    <- state_encrypted: copied directly
        state/goals.md.age      <- state_plain: encrypted on-the-fly
        config/...yaml.age      <- config: encrypted on-the-fly

    Returns the number of files packed into the ZIP (0 on failure).

    Auto-validation: after a successful snapshot, if last_validate is absent or
    > 7 days old, do_validate_backup() is triggered automatically (~3s, non-fatal
    on failure — see bkp-rst.md §4 Step 2 item 6 and R11).
    """
    backup_dir = _config["BACKUP_DIR"]

    if today is None:
        today = datetime.now(timezone.utc).date()
    tier     = _get_backup_tier(today)
    date_str = today.isoformat()
    tier_dir = backup_dir / tier
    try:
        tier_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log(f"BACKUP_MKDIR_FAILED | tier: {tier} | error: {exc}")
        return 0

    restore_paths = [e.get("restore_path") for e in registry]
    if (
        any(not _safe_relative_path(path) for path in restore_paths)
        or len(restore_paths) != len(set(restore_paths))
        or any(e.get("source_type") not in _SOURCE_TYPES for e in registry)
    ):
        log("BACKUP_ABORT | reason: invalid_registry")
        return 0

    need_encrypt = any(e["source_type"] != "state_encrypted" for e in registry)
    pubkey: "str | None" = None
    if need_encrypt:
        try:
            pubkey = get_public_key()
        except SystemExit:
            log("BACKUP_ABORT | reason: age_recipient_missing")
            return 0

    zip_name = f"{date_str}.zip"
    dest     = tier_dir / zip_name
    dest_tmp = Path(str(dest) + ".tmp")

    internal_files: dict[str, dict[str, Any]] = {}
    staged_payloads: list[Path] = []
    stream_count = 0
    payload_number = 0

    try:
        with tempfile.TemporaryDirectory(prefix="artha_backup_") as staging:
            staging_path = Path(staging)

            def put_payload(content: bytes, *, encrypt: bool) -> dict[str, Any]:
                nonlocal payload_number
                payload_number += 1
                entry_name = f"payload/{payload_number:08d}.age"
                staged = staging_path / entry_name
                staged.parent.mkdir(parents=True, exist_ok=True)
                payload = _age_encrypt_bytes(pubkey or "", content) if encrypt else content
                staged.write_bytes(payload)
                staged_payloads.append(staged)
                return {
                    "entry": entry_name,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                }

            for entry in registry:
                name = entry["name"]
                source_type = entry["source_type"]
                source_path = Path(entry["source_path"])
                restore_path = entry["restore_path"]
                if not source_path.exists():
                    log(f"BACKUP_ABORT | name: {name} | reason: missing_source")
                    return 0
                if source_type == "state_encrypted" and not is_valid_age_file(source_path):
                    log(f"BACKUP_ABORT | name: {name} | reason: invalid_age_file")
                    return 0

                if source_type == "local_db":
                    try:
                        database_image = _sqlite_online_snapshot(source_path)
                        _validate_sqlite_image(database_image)
                        data_descriptor = put_payload(database_image, encrypt=True)
                    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
                        log(f"BACKUP_ABORT | name: {name} | reason: sqlite_snapshot_failed | error: {exc}")
                        return 0
                elif source_type == "state_encrypted":
                    payload_number += 1
                    entry_name = f"payload/{payload_number:08d}.age"
                    staged = staging_path / entry_name
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source_path, staged)
                    staged_payloads.append(staged)
                    data_descriptor = {
                        "entry": entry_name,
                        "sha256": _file_sha256(staged),
                        "size": staged.stat().st_size,
                    }
                else:
                    payload_number += 1
                    entry_name = f"payload/{payload_number:08d}.age"
                    staged = staging_path / entry_name
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    if not pubkey or not age_encrypt(pubkey, source_path, staged) or not staged.is_file():
                        log(f"BACKUP_ABORT | name: {name} | reason: encrypt_failed")
                        return 0
                    staged_payloads.append(staged)
                    data_descriptor = {
                        "entry": entry_name,
                        "sha256": _file_sha256(staged),
                        "size": staged.stat().st_size,
                    }

                try:
                    named_streams = _read_named_streams(source_path)
                    if named_streams and not pubkey:
                        pubkey = get_public_key()
                    stream_descriptors = {}
                    for stream_name, stream_content in named_streams.items():
                        if not _safe_stream_name(stream_name):
                            raise ValueError(f"unsafe named-stream name: {stream_name!r}")
                        if not pubkey:
                            raise RuntimeError("age recipient is unavailable for named-stream encryption")
                        stream_descriptors[stream_name] = put_payload(stream_content, encrypt=True)
                        stream_count += 1
                except (OSError, RuntimeError, ValueError, SystemExit) as exc:
                    log(f"BACKUP_ABORT | name: {name} | reason: named_stream_capture_failed | error: {exc}")
                    return 0

                internal_files[restore_path] = {
                    "restore_path": restore_path,
                    "source_type": source_type,
                    "name": name,
                    "data": data_descriptor,
                    "streams": stream_descriptors,
                }
                log(f"BACKUP_OK | name: {name} | tier: {tier} | date: {date_str}")

            if not internal_files:
                return 0

            internal_manifest = {
                "artha_backup_version": "3",
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "date": date_str,
                "tier": tier,
                "files": internal_files,
            }
            manifest_path = staging_path / "manifest.json"
            manifest_path.write_text(json.dumps(internal_manifest, indent=2, sort_keys=True) + "\n",
                                     encoding="utf-8")
            with zipfile.ZipFile(dest_tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for staged in sorted(staged_payloads):
                    archive.write(staged, staged.relative_to(staging_path).as_posix())
                archive.write(manifest_path, "manifest.json")
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        dest_tmp.unlink(missing_ok=True)
        log(f"BACKUP_ZIP_FAILED | tier: {tier} | date: {date_str} | error: {exc}")
        return 0

    rollback_path = None
    preserve_rollback = False
    zip_sha256 = _file_sha256(dest_tmp)
    key = f"{tier}/{zip_name}"
    try:
        with _publication_lock():
            outer = _load_manifest()
            outer.setdefault("snapshots", {})
            if dest.exists():
                fd, rollback_name = tempfile.mkstemp(prefix=f"{zip_name}.rollback-", dir=tier_dir)
                os.close(fd)
                rollback_path = Path(rollback_name)
                shutil.copy2(dest, rollback_path)
            outer["snapshots"][key] = {
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "date": date_str,
                "file_count": len(internal_files),
                "stream_count": stream_count,
                "sha256": zip_sha256,
                "size": dest_tmp.stat().st_size,
                "tier": tier,
                "domain_checksums": {
                    item["name"]: item["data"]["sha256"] for item in internal_files.values()
                },
            }
            os.replace(dest_tmp, dest)
            try:
                _save_manifest_unlocked(outer)
            except Exception:
                if rollback_path is not None:
                    try:
                        os.replace(rollback_path, dest)
                        rollback_path = None
                    except OSError:
                        preserve_rollback = True
                        raise
                else:
                    dest.unlink(missing_ok=True)
                raise
    except Exception as exc:
        dest_tmp.unlink(missing_ok=True)
        if rollback_path is not None and not preserve_rollback:
            rollback_path.unlink(missing_ok=True)
        log(f"BACKUP_PUBLISH_FAILED | tier: {tier} | date: {date_str} | error: {exc}")
        return 0
    if rollback_path is not None:
        rollback_path.unlink(missing_ok=True)

    backed_up = len(internal_files)

    # Prune old ZIPs per tier
    for t, keep_n in GFS_RETENTION.items():
        if keep_n is not None:
            try:
                _prune_backups(t, keep_n)
            except Exception as exc:
                log(f"BACKUP_PRUNE_FAILED | tier: {t} | error: {exc}")

    print(f"  GFS backup: {backed_up} file(s) -> {tier}/{zip_name}")

    # Auto-validate if overdue (>7 days since last validation) — non-fatal on failure
    # bkp-rst.md R11: wrap in try/except SystemExit to prevent aborting the caller
    manifest = _load_manifest()
    last_validate = manifest.get("last_validate")
    if last_validate and backed_up > 0:
        try:
            last_dt    = datetime.fromisoformat(last_validate.replace("Z", "+00:00"))
            days_since = (datetime.now(timezone.utc) - last_dt).days
            if days_since > 7:
                log(f"AUTO_VALIDATE | reason: overdue | days_since: {days_since}")
                try:
                    do_validate_backup()
                except SystemExit:
                    log("AUTO_VALIDATE_FAILED | non_fatal | snapshot_was_created_successfully")
        except ValueError:
            pass
    elif not last_validate and backed_up > 0:
        # Never validated — trigger first validation
        log("AUTO_VALIDATE | reason: never_validated")
        try:
            do_validate_backup()
        except SystemExit:
            log("AUTO_VALIDATE_FAILED | non_fatal | snapshot_was_created_successfully")

    # KB database backup (non-fatal) — runs after prune + validate
    knowledge_graph = None
    try:
        from lib.knowledge_graph import KnowledgeGraph  # noqa: PLC0415
        knowledge_graph = KnowledgeGraph()
        knowledge_graph.backup(tier=tier)
        log(f"KB_BACKUP_OK | tier: {tier}")
    except Exception as kb_exc:
        log(f"KB_BACKUP_FAILED | tier: {tier} | error: {kb_exc}")
    finally:
        if knowledge_graph is not None:
            try:
                knowledge_graph.close()
            except Exception as close_exc:
                log(f"KB_CLOSE_FAILED | tier: {tier} | error: {close_exc}")

    return backed_up


def _select_backup_zip(date_str: "str | None", manifest: dict) -> "str | None":
    """Return the outer-manifest key (e.g. 'daily/2026-03-14.zip') to use.

    date_str given → find first matching ZIP across any tier.
    Otherwise      → newest ZIP by date field.
    """
    snapshots = {
        key: meta for key, meta in manifest.get("snapshots", {}).items()
        if _safe_relative_path(key)
        and len(key.split("/")) == 2
        and key.split("/", 1)[0] in GFS_RETENTION
        and key.lower().endswith(".zip")
        and isinstance(meta.get("date"), str)
    }
    if not snapshots:
        return None
    if date_str:
        matches = [k for k, v in snapshots.items() if v.get("date") == date_str]
        return matches[0] if matches else None
    return max(snapshots, key=lambda k: snapshots[k].get("date", ""))


def _restore_from_zip(
    zip_path:  Path,
    domain:    "str | None",
    dry_run:   bool,
    data_only: bool = False,
    confirm:   bool = False,
    expected_catalog: dict | None = None,
) -> None:
    """Core restore logic shared by do_restore() and do_install().

    For each file in the ZIP's internal manifest:
      state_encrypted → write .age file directly to restore_path
      state_plain     → age_decrypt to restore_path (plain .md)
      config          → age_decrypt to restore_path (original config file)

    data_only=True skips config files — restores only state_encrypted and
    state_plain entries.  Useful when you want to load personal data onto an
    already-configured system without overwriting its config.

    confirm=True is required to actually write files (unless dry_run).
    If neither dry_run nor confirm is set, a preview is shown and the user is
    told to add --confirm (#7).

    SHA-256 verified before writing. Existing files overwritten.
    """
    artha_dir = _config["ARTHA_DIR"]
    if not zip_path.exists():
        die(f"Backup ZIP not found: {zip_path}")
    if expected_catalog is not None:
        expected_digest = expected_catalog.get("sha256")
        try:
            actual_digest = _file_sha256(zip_path)
        except OSError:
            die(f"Backup ZIP not found: {zip_path}")
        if not isinstance(expected_digest, str) or actual_digest != expected_digest:
            die(f"Backup ZIP checksum failed: {zip_path.name}")
    try:
        zf_context = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile as exc:
        die(f"Corrupt ZIP file {zip_path.name}: {exc}")

    with zf_context as zf:
        try:
            internal = json.loads(zf.read("manifest.json").decode("utf-8"),
                                  object_pairs_hook=_unique_json_pairs)
            version, records = _archive_records(zf, internal, expected_catalog)
            for record in records:
                _safe_destination(artha_dir, record["restore_path"])
            if any(record["streams"] for record in records) and not _supports_named_streams():
                raise ValueError("native named-stream support is unavailable; refusing stream-bearing restore")
        except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            die(f"Backup archive validation failed: {exc}")

        selected = records
        if data_only:
            selected = [record for record in selected if record["source_type"] != "config"]
        if domain:
            selected = [record for record in selected if record["name"] == domain]
            if not selected:
                print(f"No files for domain {domain!r} in {zip_path.name}.")
                sys.exit(1)

        snap_date = internal.get("date", "unknown")
        snap_tier = internal.get("tier", "unknown")
        scope_note = " (state only — config skipped)" if data_only else ""
        if dry_run or not confirm:
            label = "DRY RUN" if dry_run else "PREVIEW"
            print(f"{label} — snapshot {snap_tier}/{snap_date} ({zip_path.name}){scope_note}\n")
            for record in sorted(selected, key=lambda item: item["restore_path"]):
                action = "copy" if record["source_type"] == "state_encrypted" else "decrypt"
                print(f"  would {action}: {record['restore_path']!s:<55} -> {record['restore_path']}")
                for stream_name in record["streams"]:
                    print(f"    named stream: {stream_name}")
            if not dry_run:
                print("\n  Add --confirm to execute this restore.")
            return

        if not check_age_installed():
            die("'age' not installed.")
        identity = get_private_key()
        try:
            verified = _verify_payloads(zf, records, identity)
        except (OSError, ValueError, RuntimeError, sqlite3.Error, zipfile.BadZipFile) as exc:
            die(f"Backup archive validation failed; no files were restored: {exc}")

        selected_paths = {record["restore_path"] for record in selected}
        selected_verified = [record for record in verified if record["restore_path"] in selected_paths]
        pre_restore_dir = _config["BACKUP_DIR"] / "pre-restore"
        if _is_link_or_reparse(pre_restore_dir):
            die("Pre-restore backup directory is linked; refusing restore.")
        pre_restore_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        backed = 0
        try:
            for record in selected_verified:
                live = _safe_destination(artha_dir, record["restore_path"])
                if not live.is_file():
                    continue
                old_streams = _read_named_streams(live)
                safe_name = record["restore_path"].replace("/", "__")
                dest_bak = pre_restore_dir / f"{ts}__{safe_name}"
                shutil.copy2(live, dest_bak)
                for stream_name, stream_content in old_streams.items():
                    _write_named_stream(dest_bak, stream_name, stream_content)
                backed += 1
        except (OSError, ValueError) as exc:
            die(f"Pre-restore protection failed; no files were restored: {exc}")
        if backed:
            log(f"PRE_RESTORE_BACKUP | files: {backed} | dir: pre-restore/ | ts: {ts}")
            print(f"  Pre-restore backup: {backed} live file(s) saved to backups/pre-restore/")

        errors = 0
        restored = 0
        for record in selected_verified:
            destination = _safe_destination(artha_dir, record["restore_path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            if _is_link_or_reparse(destination):
                print(f"  \u2717 WRITE FAILED: linked destination {record['restore_path']}")
                errors += 1
                continue
            staged = None
            try:
                fd, staged_name = tempfile.mkstemp(
                    prefix=".artha-restore-", suffix=".tmp", dir=destination.parent,
                )
                staged = Path(staged_name)
                content = record["ciphertext"] if record["source_type"] == "state_encrypted" else record["plaintext"]
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                for stream_name, stream_content in record["stream_values"].items():
                    _write_named_stream(staged, stream_name, stream_content)
                os.replace(staged, destination)
            except (OSError, ValueError) as exc:
                if staged is not None:
                    staged.unlink(missing_ok=True)
                print(f"  \u2717 WRITE FAILED: {record['restore_path']}: {exc}")
                log(f"RESTORE_FAIL | zip: {zip_path.name} | file: {record['restore_path']} | reason: write_error")
                errors += 1
                continue
            print(f"  \u2713 {record['restore_path']}")
            log(f"RESTORE_OK | zip: {zip_path.name} | file: {record['restore_path']} "
                f"| type: {record['source_type']} | streams: {len(record['streams'])}")
            restored += 1

    print()
    if errors == 0:
        print(f"Restore complete: {restored} file(s) restored from {zip_path.name} (version {version}).")
    else:
        print(f"Restore: {restored} restored, {errors} failed.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Public backup operations
# ---------------------------------------------------------------------------

def get_health_summary() -> tuple:
    """Return (snapshot_count, last_validate_iso, validation_errors) for vault health check."""
    manifest = _load_manifest()
    return (
        len(manifest.get("snapshots", {})),
        manifest.get("last_validate"),
        manifest.get("last_validate_errors", 0),
    )


def do_backup_status() -> None:
    """Show GFS backup catalog: ZIP names, tier counts, and last validation date."""
    manifest      = _load_manifest()
    snapshots     = manifest.get("snapshots", {})
    last_validate = manifest.get("last_validate")

    print("\u2501" * 60)
    print(f"VAULT BACKUP STATUS \u2014 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\u2501" * 60)

    if last_validate:
        try:
            last_dt    = datetime.fromisoformat(last_validate.replace("Z", "+00:00"))
            days_since = (datetime.now(timezone.utc) - last_dt).days
            if days_since <= 35:
                status = f"\u2713 {days_since}d ago"
            else:
                status = f"\u26a0 {days_since}d ago (overdue \u2014 run: backup.py validate)"
        except ValueError:
            status = last_validate
        print(f"  Last validation : {status}")
    else:
        print("  Last validation : \u26a0 NEVER \u2014 run: backup.py validate")

    print()
    if not snapshots:
        print("  No backups found. Run backup.py snapshot (or vault.py encrypt) to create the first backup.")
    else:
        for tier in ("yearly", "monthly", "weekly", "daily"):
            tier_snaps = {k: v for k, v in snapshots.items() if v.get("tier") == tier}
            if not tier_snaps:
                continue
            keep_n = GFS_RETENTION.get(tier)
            label  = str(keep_n) if keep_n else "\u221e"
            print(f"  {tier.upper():<8}  {len(tier_snaps)} snapshot(s), keep={label}")
            for key in sorted(tier_snaps, key=lambda k: tier_snaps[k].get("date", ""), reverse=True)[:3]:
                meta = tier_snaps[key]
                size_kb = meta.get("size", 0) / 1024
                print(f"            {meta['date']}  {Path(key).name:<30} {meta.get('file_count', '?')} files  {size_kb:.1f} KB")
            if len(tier_snaps) > 3:
                print(f"            \u2026 and {len(tier_snaps) - 3} older snapshot(s)")
    print("\u2501" * 60)


def do_validate_backup(
    domain:   "str | None" = None,
    date_str: "str | None" = None,
) -> None:
    """Validate selected encrypted payloads entirely in memory."""
    backup_dir = _config["BACKUP_DIR"]

    if not check_age_installed():
        die("'age' not installed.")
    privkey  = get_private_key()
    manifest = _load_manifest()

    def fail_validation(message: str, reason: str) -> NoReturn:
        print(message)
        log(f"BACKUP_VALIDATE_FAIL | reason: {reason}")
        manifest["last_validate_errors"] = 1
        _save_manifest(manifest)
        sys.exit(1)

    if not manifest.get("snapshots"):
        fail_validation(
            "No backups found. Run backup.py snapshot first to create initial backups.",
            "no_backups",
        )

    zip_key = _select_backup_zip(date_str, manifest)
    if not zip_key:
        print(f"No matching backup snapshot found (date={date_str!r}).")
        sys.exit(1)

    zip_path = backup_dir / zip_key

    # Verify outer ZIP checksum first
    expected_zip_sha256 = manifest["snapshots"][zip_key].get("sha256", "")
    if not expected_zip_sha256 or _file_sha256(zip_path) != expected_zip_sha256:
        fail_validation(
            f"  \u2717 ZIP CHECKSUM FAIL: {zip_key}",
            f"zip_checksum_missing_or_mismatch | zip: {zip_key}",
        )

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            internal = json.loads(zf.read("manifest.json").decode("utf-8"),
                                  object_pairs_hook=_unique_json_pairs)
            version, records = _archive_records(
                zf, internal, manifest["snapshots"][zip_key],
            )
            selected = records
            if domain:
                selected = [record for record in records if record["name"] == domain]
                if not selected:
                    print(f"No files for domain {domain!r} in {zip_key}.")
                    sys.exit(1)
            verified = _verify_payloads(zf, selected, privkey)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RuntimeError,
            KeyError, zipfile.BadZipFile, sqlite3.Error) as exc:
        fail_validation(
            f"  \u2717 BACKUP VALIDATION FAIL: {zip_key}: {exc}",
            f"archive_or_payload_invalid | zip: {zip_key}",
        )

    for record in verified:
        digest = record["data"]["sha256"]
        log(f"BACKUP_VALIDATE_OK | zip: {zip_key} | file: {record['restore_path']} "
            f"| type: {record['source_type']} | streams: {len(record['streams'])} | sha256: {digest[:16]}")
        print(f"  \u2713 {record['restore_path']} type={record['source_type']} "
              f"streams={len(record['streams'])} sha256={digest[:14]}...")

    if domain is None:
        manifest["last_validate"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        manifest.pop("last_validate_errors", None)
        _save_manifest(manifest)
    print(f"Backup validation: \u2713 {len(verified)} file(s) valid  [{zip_key}]"
          f" (version {version})")


def do_restore(
    date_str:  "str | None" = None,
    domain:    "str | None" = None,
    dry_run:   bool = False,
    data_only: bool = False,
    confirm:   bool = False,
) -> None:
    """Restore from the GFS backup catalog — finds the right ZIP automatically.

    backup.py restore [--date YYYY-MM-DD] [--domain DOMAIN] [--dry-run]
                      [--data-only] [--confirm]

    --data-only  Restore state files only (skip config files).  Use this when
                 Artha is already configured on the running system and you only
                 want to refresh personal data from the backup.
    --confirm    Required to actually write files (safety gate #7).
    """
    backup_dir = _config["BACKUP_DIR"]

    if not check_age_installed():
        die("'age' not installed.")
    manifest = _load_manifest()

    if not manifest.get("snapshots"):
        print("No backups found. Run backup.py snapshot first to create initial backups.")
        return

    zip_key = _select_backup_zip(date_str, manifest)
    if not zip_key:
        print(f"No matching backup snapshot (date={date_str!r}).")
        sys.exit(1)

    zip_path = backup_dir / zip_key
    scope = "state only" if data_only else "state + config"
    print(f"Restoring from {zip_key} ({scope}) ...")
    _restore_from_zip(
        zip_path, domain, dry_run, data_only=data_only, confirm=confirm,
        expected_catalog=manifest["snapshots"][zip_key],
    )


def do_install(
    zip_path_str: str,
    dry_run:   bool = False,
    data_only: bool = False,
    confirm:   bool = False,
) -> None:
    """Restore from an explicit backup ZIP file — for cold-start on a new machine.

    backup.py install <path/to/YYYY-MM-DD.zip> [--dry-run] [--data-only] [--confirm]

    The ZIP is self-contained: it carries its own internal manifest with SHA-256
    checksums and restore paths for every file.  No catalog access needed.

    --data-only  Restore state files only (skip config files).
    --confirm    Required to actually write files (safety gate #7).
    """
    zip_path = Path(zip_path_str).expanduser().resolve()
    scope = "state only" if data_only else "state + config"
    print(f"Installing from backup: {zip_path.name} ({scope}) ...")
    _restore_from_zip(zip_path, domain=None, dry_run=dry_run, data_only=data_only, confirm=confirm)


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

def do_export_key() -> None:
    """Print the age private key to stdout with security warnings.

    The age private key is the single point of failure — without it every
    backup ZIP is permanently unrecoverable. Export and store securely in a
    password manager or printed safe copy. Never commit or email this key.
    """
    import keyring as _kr
    key = _kr.get_password(_config["KC_SERVICE"], _config["KC_ACCOUNT"])
    if not key:
        die("No private key found in credential store.")
    print("=" * 60)
    print("\u26a0  AGE PRIVATE KEY \u2014 HANDLE WITH EXTREME CARE")
    print("=" * 60)
    print()
    print("Store this key securely (password manager, printed safe copy).")
    print("Anyone with this key can decrypt ALL your Artha state files.")
    print("Do NOT commit this to git, email it, or store it in cloud notes.")
    print()
    print(key)
    print()
    print("=" * 60)
    log("KEY_EXPORTED | action: private_key_displayed_to_stdout")

    # Record export timestamp in manifest for health check (#3)
    manifest = _load_manifest()
    manifest["last_key_export"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_manifest(manifest)


def do_import_key() -> None:
    """Read an age private key from stdin and store in the credential store.

    Reads from stdin (not command-line argument) to avoid the key appearing
    in shell history.
    """
    import keyring as _kr
    svc  = _config["KC_SERVICE"]
    acct = _config["KC_ACCOUNT"]
    print("Paste your age private key (starts with AGE-SECRET-KEY-1...):")
    print("Press Enter, then Ctrl-D (macOS/Linux) or Ctrl-Z+Enter (Windows) when done.")
    key = sys.stdin.read().strip()
    if not key.startswith("AGE-SECRET-KEY-"):
        die("Invalid key format. Age private keys start with 'AGE-SECRET-KEY-'.")
    _kr.set_password(svc, acct, key)
    print(f"\u2713 Private key stored in credential store (service={svc}, account={acct}).")
    log("KEY_IMPORTED | action: private_key_stored_in_credential_store")


# ---------------------------------------------------------------------------
# Preflight delegation
# ---------------------------------------------------------------------------

def do_file_snapshot(file_path: Path) -> int:
    """Create a lightweight `.pre-write.bak` copy of a single file.

    Used by Step 8t (Ambient Intent Buffer) as a pre-write guard before
    materializing planning objects. The .bak file provides a one-step rollback
    if post-write YAML validation fails.

    Returns 0 on success, 1 on failure.
    Silently succeeds when the file does not yet exist (first write).
    """
    if not file_path.exists():
        # First write to a new file — nothing to snapshot, not an error
        return 0
    bak_path = Path(str(file_path) + ".pre-write.bak")
    try:
        shutil.copy2(str(file_path), str(bak_path))
        log(f"FILE_SNAPSHOT | source: {file_path} | bak: {bak_path.name}")
        print(f"snapshot: {file_path.name} → {bak_path.name}")
        return 0
    except OSError as exc:
        log(f"FILE_SNAPSHOT_FAILED | source: {file_path} | error: {exc}")
        print(f"error: snapshot failed for {file_path}: {exc}", file=sys.stderr)
        return 1


def _do_preflight() -> None:
    """Check prerequisites for backup/restore by delegating to vault.py health.

    Delegates to `vault.py health` to avoid duplicating health-check logic.
    bkp-rst.md §4.8: testing this subprocess call is out of scope — covered by
    the test_help_output check that the subcommand appears in --help.
    """
    artha_dir = _config["ARTHA_DIR"]
    result = subprocess.run(
        [sys.executable, str(artha_dir / "scripts" / "vault.py"), "health"],
        capture_output=True, text=True, cwd=str(artha_dir),
        env={**os.environ, "ARTHA_NO_REEXEC": "1"},
    )
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: "list[str] | None" = None) -> None:
    """CLI entry point.

    Pass argv for programmatic/test invocation; None reads sys.argv.
    """
    parser = argparse.ArgumentParser(
        prog="backup.py",
        description="Artha GFS backup manager — archive, restore, validate, and manage state snapshots.",
    )
    sub = parser.add_subparsers(dest="command")

    # ── Archive operations ──
    snap = sub.add_parser("snapshot", help="Create a GFS backup snapshot now")
    snap.add_argument(
        "--tier",
        choices=["daily", "weekly", "monthly", "yearly"],
        help="Force a specific tier (default: auto from today's date)",
    )

    sub.add_parser("status", help="Show backup catalog and validation status")

    val = sub.add_parser("validate", help="Validate backup integrity")
    val.add_argument("--domain", help="Validate one domain only")
    val.add_argument("--date", help="Validate a specific date (YYYY-MM-DD)")

    # ── Restore operations ──
    rst = sub.add_parser("restore", help="Restore from GFS backup catalog")
    rst.add_argument("--domain", help="Restore one domain only")
    rst.add_argument("--date", help="Restore a specific date (YYYY-MM-DD)")
    rst.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    rst.add_argument("--data-only", action="store_true", help="Restore state files only (skip config)")
    rst.add_argument("--confirm", action="store_true", help="Required to actually write files (safety gate)")

    inst = sub.add_parser("install", help="Restore from an explicit backup ZIP file")
    inst.add_argument("zipfile", help="Path to the backup ZIP file")
    inst.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    inst.add_argument("--data-only", action="store_true", help="Restore state files only (skip config)")
    inst.add_argument("--confirm", action="store_true", help="Required to actually write files (safety gate)")

    # ── Single-file snapshot ──
    fs = sub.add_parser(
        "file-snapshot",
        help="Copy a single file to <file>.pre-write.bak (pre-write guard for Step 8t)",
    )
    fs.add_argument("file", help="Path to the file to snapshot")

    # ── Key management ──
    sub.add_parser("export-key", help="Display the age private key (for secure backup)")
    sub.add_parser("import-key", help="Store an age private key in the credential store")

    # ── Diagnostics ──
    sub.add_parser("preflight", help="Check prerequisites (delegates to vault.py health)")

    args = parser.parse_args(argv)

    if args.command == "snapshot":
        registry = load_backup_registry()
        count = backup_snapshot(registry)
        if count == 0:
            die("Snapshot failed — no files archived.")
    elif args.command == "status":
        do_backup_status()
    elif args.command == "validate":
        do_validate_backup(domain=args.domain, date_str=args.date)
    elif args.command == "restore":
        do_restore(
            date_str=args.date, domain=args.domain,
            dry_run=args.dry_run, data_only=args.data_only,
            confirm=args.confirm,
        )
    elif args.command == "install":
        do_install(args.zipfile, dry_run=args.dry_run, data_only=args.data_only,
                   confirm=args.confirm)
    elif args.command == "file-snapshot":
        sys.exit(do_file_snapshot(Path(args.file)))
    elif args.command == "export-key":
        do_export_key()
    elif args.command == "import-key":
        do_import_key()
    elif args.command == "preflight":
        _do_preflight()
    else:
        parser.print_help()
        sys.exit(1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Config (backup-specific entries are stored in foundation._config)
    "BACKUP_DIR", "BACKUP_MANIFEST", "GFS_RETENTION",
    # Registry
    "load_backup_registry",
    # Core operations
    "backup_snapshot", "get_health_summary",
    # User-facing commands
    "do_backup_status", "do_validate_backup", "do_restore", "do_install",
    # Key management
    "do_export_key", "do_import_key",
]


if __name__ == "__main__":
    main()
