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
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import date as _date_type, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
    is_valid_age_file,
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
      source_type  — 'state_encrypted' | 'state_plain' | 'config'
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


def _load_manifest() -> dict:
    """Load backups/manifest.json; return empty structure on missing or corrupt file."""
    manifest_path = _config["BACKUP_MANIFEST"]
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"last_validate": None, "snapshots": {}}


def _save_manifest(manifest: dict) -> None:
    """Atomically overwrite manifest.json via a .tmp sibling."""
    manifest_path = _config["BACKUP_MANIFEST"]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(manifest_path) + ".tmp")
    try:
        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(manifest_path))
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        log(f"MANIFEST_SAVE_FAILED | error: {exc}")


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
    """Remove oldest ZIP snapshots in tier_dir beyond keep_n retention limit.

    Last-known-good protection (#6): before deleting a ZIP, verify that every
    domain checksum it contains also exists in at least one other retained
    snapshot (across ALL tiers). If a ZIP is the sole carrier of a domain's
    checksum, it is pinned and not pruned.
    """
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

        try:
            old_zip.unlink()
            snapshots.pop(key, None)
            log(f"BACKUP_PRUNED | zip: {old_zip.name} | tier: {tier} | retention: {keep_n}")
        except OSError as exc:
            log(f"BACKUP_PRUNE_FAILED | zip: {old_zip.name} | error: {exc}")
    _save_manifest(manifest)


def _zip_archive_path(entry: dict) -> str:
    """Return the path of this entry's file inside the backup ZIP archive.

    state_encrypted: restore_path is already '.age' — use as-is.
    state_plain / config: restore_path ends in '.md' or has no '.age' suffix —
        append '.age' because we encrypt on-the-fly before storing.
    """
    if entry["source_type"] == "state_encrypted":
        return entry["restore_path"]      # e.g. 'state/finance.md.age'
    return entry["restore_path"] + ".age" # e.g. 'state/goals.md.age'


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

    need_encrypt = any(e["source_type"] != "state_encrypted" for e in registry)
    pubkey: "str | None" = None
    if need_encrypt:
        try:
            pubkey = get_public_key()
        except SystemExit:
            log("BACKUP_PUBKEY_MISSING | plain/config files will be skipped this run")

    zip_name = f"{date_str}.zip"
    dest     = tier_dir / zip_name
    dest_tmp = Path(str(dest) + ".tmp")

    internal_files: dict = {}
    backed_up = 0

    with tempfile.TemporaryDirectory(prefix="artha_backup_") as staging:
        staging_path = Path(staging)

        for entry in registry:
            name         = entry["name"]
            source_type  = entry["source_type"]
            source_path  = entry["source_path"]
            restore_path = entry["restore_path"]
            arc_path     = _zip_archive_path(entry)

            if not source_path.exists():
                continue

            staged = staging_path / arc_path
            staged.parent.mkdir(parents=True, exist_ok=True)

            try:
                if source_type == "state_encrypted":
                    if not is_valid_age_file(source_path):
                        size = source_path.stat().st_size
                        log(f"BACKUP_SKIP | name: {name} | reason: invalid_age_file (size={size})")
                        continue
                    shutil.copy2(str(source_path), str(staged))
                else:
                    if not pubkey:
                        log(f"BACKUP_SKIP | name: {name} | reason: no_pubkey")
                        continue
                    if not age_encrypt(pubkey, source_path, staged):
                        staged.unlink(missing_ok=True)
                        log(f"BACKUP_ENCRYPT_FAILED | name: {name}")
                        continue
                    if not staged.exists():
                        log(f"BACKUP_ENCRYPT_FAILED | name: {name} | reason: no_output")
                        continue
            except OSError as exc:
                staged.unlink(missing_ok=True)
                log(f"BACKUP_STAGE_FAILED | name: {name} | error: {exc}")
                continue

            sha256 = _file_sha256(staged)
            internal_files[arc_path] = {
                "name":         name,
                "restore_path": restore_path,
                "sha256":       sha256,
                "size":         staged.stat().st_size,
                "source_type":  source_type,
            }
            backed_up += 1
            log(f"BACKUP_OK | name: {name} | tier: {tier} | date: {date_str} | sha256: {sha256[:16]}...")

        if backed_up == 0:
            return 0

        # Write internal manifest.json into staging dir
        internal_manifest = {
            "artha_backup_version": "2",
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "date":    date_str,
            "tier":    tier,
            "files":   internal_files,
        }
        (staging_path / "manifest.json").write_text(
            json.dumps(internal_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        # Pack staging dir into ZIP atomically
        try:
            with zipfile.ZipFile(str(dest_tmp), "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for abs_p in sorted(staging_path.rglob("*")):
                    if abs_p.is_file():
                        zf.write(str(abs_p), str(abs_p.relative_to(staging_path)))
            os.replace(str(dest_tmp), str(dest))
        except OSError as exc:
            dest_tmp.unlink(missing_ok=True)
            log(f"BACKUP_ZIP_FAILED | tier: {tier} | date: {date_str} | error: {exc}")
            return 0

    # Update outer manifest catalog (with per-domain checksums for prune protection #6)
    zip_sha256 = _file_sha256(dest)
    domain_checksums = {
        meta["name"]: meta["sha256"]
        for meta in internal_files.values()
    }
    outer = _load_manifest()
    outer.setdefault("snapshots", {})
    outer["snapshots"][f"{tier}/{zip_name}"] = {
        "created":           datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date":              date_str,
        "file_count":        backed_up,
        "sha256":            zip_sha256,
        "size":              dest.stat().st_size,
        "tier":              tier,
        "domain_checksums":  domain_checksums,
    }
    _save_manifest(outer)

    # Prune old ZIPs per tier
    for t, keep_n in GFS_RETENTION.items():
        if keep_n is not None:
            _prune_backups(t, keep_n)

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
    try:
        from lib.knowledge_graph import KnowledgeGraph  # noqa: PLC0415
        KnowledgeGraph().backup(tier=tier)
        log(f"KB_BACKUP_OK | tier: {tier}")
    except Exception as kb_exc:
        log(f"KB_BACKUP_FAILED | tier: {tier} | error: {kb_exc}")

    return backed_up


def _select_backup_zip(date_str: "str | None", manifest: dict) -> "str | None":
    """Return the outer-manifest key (e.g. 'daily/2026-03-14.zip') to use.

    date_str given → find first matching ZIP across any tier.
    Otherwise      → newest ZIP by date field.
    """
    snapshots = manifest.get("snapshots", {})
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

    if not check_age_installed():
        die("'age' not installed.")
    privkey = get_private_key()

    if not zip_path.exists():
        die(f"Backup ZIP not found: {zip_path}")

    try:
        zf_handle = zipfile.ZipFile(str(zip_path), "r")
    except zipfile.BadZipFile as exc:
        die(f"Corrupt ZIP file {zip_path.name}: {exc}")

    with zf_handle as zf:
        try:
            with zf.open("manifest.json") as mf:
                internal = json.load(mf)
        except Exception as exc:
            die(f"Cannot read internal manifest in {zip_path.name}: {exc}")

        files = internal.get("files", {})
        if data_only:
            files = {k: v for k, v in files.items() if v.get("source_type") != "config"}
        if domain:
            files = {k: v for k, v in files.items() if v.get("name") == domain}
            if not files:
                print(f"No files for domain {domain!r} in {zip_path.name}.")
                sys.exit(1)

        snap_date  = internal.get("date", "unknown")
        snap_tier  = internal.get("tier", "unknown")
        scope_note = " (state only — config skipped)" if data_only else ""
        if dry_run:
            print(f"DRY RUN — snapshot {snap_tier}/{snap_date} ({zip_path.name}){scope_note} — no files will be written\n")
        elif not confirm:
            # Show preview and require --confirm (#7)
            print(f"PREVIEW — snapshot {snap_tier}/{snap_date} ({zip_path.name}){scope_note}\n")
            for arc_path, meta in sorted(files.items()):
                restore_path = meta.get("restore_path", "")
                source_type  = meta.get("source_type", "state_encrypted")
                action = "copy" if source_type == "state_encrypted" else "decrypt"
                print(f"  would {action}: {arc_path!s:<55} -> {restore_path}")
            print(f"\n  Add --confirm to execute this restore.")
            return

        zip_ver = internal.get("artha_backup_version", "unknown")
        if zip_ver != "2":
            print(f"  WARNING: ZIP backup version {zip_ver} — expected 2. Attempting restore.")

        errors   = 0
        restored = 0

        # Pre-restore backup: snapshot current live files before overwriting (#7)
        if not dry_run:
            pre_restore_dir = _config["BACKUP_DIR"] / "pre-restore"
            pre_restore_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            backed = 0
            for arc_path, meta in files.items():
                rp = meta.get("restore_path", "")
                live = artha_dir / rp
                if live.exists():
                    safe_name = rp.replace("/", "__").replace("\\\\", "__")
                    dest_bak = pre_restore_dir / f"{ts}__{safe_name}"
                    try:
                        shutil.copy2(str(live), str(dest_bak))
                        backed += 1
                    except OSError:
                        pass
            if backed:
                log(f"PRE_RESTORE_BACKUP | files: {backed} | dir: pre-restore/ | ts: {ts}")
                print(f"  Pre-restore backup: {backed} live file(s) saved to backups/pre-restore/")

        with tempfile.TemporaryDirectory(prefix="artha_restore_") as tmpdir:
            tmppath = Path(tmpdir)

            for arc_path, meta in sorted(files.items()):
                restore_path = meta.get("restore_path", "")
                source_type  = meta.get("source_type", "state_encrypted")
                dest         = artha_dir / restore_path

                # Read from ZIP and verify SHA-256
                try:
                    zip_data = zf.read(arc_path)
                except KeyError:
                    print(f"  \u2717 MISSING in ZIP:  {arc_path}")
                    log(f"RESTORE_FAIL | zip: {zip_path.name} | file: {arc_path} | reason: missing_in_zip")
                    errors += 1
                    continue

                actual_sha256   = hashlib.sha256(zip_data).hexdigest()
                expected_sha256 = meta.get("sha256", "")
                if expected_sha256 and actual_sha256 != expected_sha256:
                    print(f"  \u2717 CHECKSUM FAIL:   {arc_path}")
                    log(f"RESTORE_FAIL | zip: {zip_path.name} | file: {arc_path} | reason: checksum_mismatch")
                    errors += 1
                    continue

                if dry_run:
                    action = "copy" if source_type == "state_encrypted" else "decrypt"
                    print(f"  would {action}: {arc_path!s:<55} -> {restore_path}")
                    continue

                dest.parent.mkdir(parents=True, exist_ok=True)
                dest_tmp = Path(str(dest) + ".tmp")

                try:
                    if source_type == "state_encrypted":
                        dest_tmp.write_bytes(zip_data)
                        os.replace(str(dest_tmp), str(dest))
                    else:
                        extracted = tmppath / Path(arc_path).name
                        extracted.write_bytes(zip_data)
                        if not age_decrypt(privkey, extracted, dest_tmp):
                            dest_tmp.unlink(missing_ok=True)
                            print(f"  \u2717 DECRYPT FAILED:  {arc_path}")
                            log(f"RESTORE_FAIL | zip: {zip_path.name} | file: {arc_path} | reason: decrypt_failed")
                            errors += 1
                            continue
                        os.replace(str(dest_tmp), str(dest))
                except OSError as exc:
                    dest_tmp.unlink(missing_ok=True)
                    print(f"  \u2717 WRITE FAILED:    {arc_path}: {exc}")
                    log(f"RESTORE_FAIL | zip: {zip_path.name} | file: {arc_path} | reason: write_error")
                    errors += 1
                    continue

                print(f"  \u2713 {restore_path}")
                log(f"RESTORE_OK | zip: {zip_path.name} | file: {arc_path} | dest: {restore_path} | type: {source_type}")
                restored += 1

    print()
    if not dry_run:
        if errors == 0:
            print(f"Restore complete: {restored} file(s) restored from {zip_path.name}.")
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
    """Validate backup ZIP — never touches live state.

    Opens the newest ZIP (or the one matching --date). For each file:
      1. SHA-256 inside ZIP matches internal manifest (bit-rot detection)
      2. age_decrypt succeeds
      3. Decrypted output non-empty
      4. YAML frontmatter (for state files)
      5. Word count >= 30 (for state files)

    Updates manifest.last_validate on full success.
    """
    backup_dir = _config["BACKUP_DIR"]

    if not check_age_installed():
        die("'age' not installed.")
    privkey  = get_private_key()
    manifest = _load_manifest()

    if not manifest.get("snapshots"):
        print("No backups found. Run backup.py snapshot first to create initial backups.")
        return

    zip_key = _select_backup_zip(date_str, manifest)
    if not zip_key:
        print(f"No matching backup snapshot found (date={date_str!r}).")
        sys.exit(1)

    zip_path = backup_dir / zip_key

    # Verify outer ZIP checksum first
    expected_zip_sha256 = manifest["snapshots"][zip_key].get("sha256", "")
    if expected_zip_sha256 and _file_sha256(zip_path) != expected_zip_sha256:
        print(f"  \u2717 ZIP CHECKSUM FAIL: {zip_key}")
        log(f"BACKUP_VALIDATE_FAIL | zip: {zip_key} | reason: zip_checksum_mismatch")
        sys.exit(1)

    errors    = 0
    validated = 0

    try:
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            try:
                with zf.open("manifest.json") as mf:
                    internal = json.load(mf)
            except Exception as exc:
                print(f"  \u2717 MANIFEST READ FAIL: {zip_key}: {exc}")
                sys.exit(1)

            files_to_check = internal.get("files", {})
            if domain:
                files_to_check = {k: v for k, v in files_to_check.items()
                                  if v.get("name") == domain}
                if not files_to_check:
                    print(f"No files for domain {domain!r} in {zip_key}.")
                    sys.exit(1)

            with tempfile.TemporaryDirectory(prefix="artha_validate_") as tmpdir:
                tmppath = Path(tmpdir)

                for arc_path, meta in sorted(files_to_check.items()):
                    source_type = meta.get("source_type", "state_encrypted")

                    # Read from ZIP
                    try:
                        zip_data = zf.read(arc_path)
                    except KeyError:
                        print(f"  \u2717 MISSING in ZIP:  {arc_path}")
                        log(f"BACKUP_VALIDATE_FAIL | zip: {zip_key} | file: {arc_path} | reason: missing_in_zip")
                        errors += 1
                        continue

                    # 1. SHA-256
                    actual_sha256   = hashlib.sha256(zip_data).hexdigest()
                    expected_sha256 = meta.get("sha256", "")
                    if expected_sha256 and actual_sha256 != expected_sha256:
                        print(f"  \u2717 CHECKSUM FAIL:   {arc_path}")
                        log(f"BACKUP_VALIDATE_FAIL | zip: {zip_key} | file: {arc_path} | reason: checksum_mismatch")
                        errors += 1
                        continue

                    # 2. Decrypt
                    extracted = tmppath / (Path(arc_path).name + ".enc")
                    decrypted = tmppath / (Path(arc_path).stem + ".dec")
                    extracted.write_bytes(zip_data)
                    if not age_decrypt(privkey, extracted, decrypted):
                        print(f"  \u2717 DECRYPT FAIL:    {arc_path}")
                        log(f"BACKUP_VALIDATE_FAIL | zip: {zip_key} | file: {arc_path} | reason: decrypt_failed")
                        errors += 1
                        continue

                    # 3. Non-empty
                    if not decrypted.exists() or decrypted.stat().st_size == 0:
                        print(f"  \u2717 EMPTY:           {arc_path}")
                        log(f"BACKUP_VALIDATE_FAIL | zip: {zip_key} | file: {arc_path} | reason: empty_content")
                        errors += 1
                        continue

                    content    = decrypted.read_text(encoding="utf-8", errors="replace")
                    word_count = len(content.split())

                    # Config files don't need YAML/word-count checks
                    if source_type == "config":
                        print(f"  \u2713 {arc_path:<55} type=config words={word_count} sha256={actual_sha256[:14]}...")
                        log(f"BACKUP_VALIDATE_OK | zip: {zip_key} | file: {arc_path} | type: config | words: {word_count}")
                        validated += 1
                        continue

                    # 4. YAML frontmatter
                    if not content.lstrip().startswith("---"):
                        print(f"  \u2717 NO YAML:         {arc_path}")
                        log(f"BACKUP_VALIDATE_FAIL | zip: {zip_key} | file: {arc_path} | reason: missing_yaml_frontmatter")
                        errors += 1
                        continue

                    # 5. Word count >= 30
                    if word_count < 30:
                        print(f"  \u2717 TOO SHORT:       {arc_path} ({word_count} words)")
                        log(f"BACKUP_VALIDATE_FAIL | zip: {zip_key} | file: {arc_path} | reason: content_too_short | words: {word_count}")
                        errors += 1
                        continue

                    print(f"  \u2713 {arc_path:<55} type={source_type:<17} words={word_count} sha256={actual_sha256[:14]}...")
                    log(f"BACKUP_VALIDATE_OK | zip: {zip_key} | file: {arc_path} | type: {source_type} | words: {word_count} | sha256: {actual_sha256[:16]}")
                    validated += 1

    except zipfile.BadZipFile as exc:
        print(f"  \u2717 CORRUPT ZIP: {zip_key}: {exc}")
        sys.exit(1)

    print()
    if validated > 0:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        manifest["last_validate"] = now_iso
        if errors > 0:
            manifest["last_validate_errors"] = errors
        else:
            manifest.pop("last_validate_errors", None)
        _save_manifest(manifest)

    if errors == 0 and validated > 0:
        print(f"Backup validation: \u2713 {validated} file(s) valid  [{zip_key}]")
    elif errors > 0 and validated > 0:
        print(f"Backup validation: \u2713 {validated} passed, \u2717 {errors} failed  [{zip_key}]")
        print(f"  last_validate updated (partial success). Fix failing files to reach full pass.")
    else:
        print(f"Backup validation: \u2717 {errors} failure(s), {validated} passed  [{zip_key}]")
        if errors > 0:
            sys.exit(1)


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
    _restore_from_zip(zip_path, domain, dry_run, data_only=data_only, confirm=confirm)


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
