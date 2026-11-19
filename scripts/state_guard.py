#!/usr/bin/env python3
"""state_guard.py - off-repo State File snapshots, restore drill, and a loud freshness/shrink check.

Why this exists: the automatic backups lived inside the repo checkout, next to ``state/``, so one
deletion removed both, and nothing alarmed because the old check only fired when a backup ran and
failed.  This tool keeps its copies OUTSIDE the checkout and judges health by time and content, not
by whether a backup job reported an error.

Commands (all exit non-zero on a problem so callers cannot miss it):
  snapshot [--force]   copy State Files to <backup root>/<UTC stamp>/ with a sha256 manifest.
                       Refuses when state shrank >20% against the newest snapshot (so a wiped
                       state can never replace a good backup) unless --force.
  verify [--no-decrypt] re-hash the newest snapshot and decrypt-test every .age file in memory.
  check [--snapshot-if-stale] [--max-age-hours N]
                       loud health check: FAIL when no snapshot exists, the newest is too old,
                       or the live state lost files against it.  With --snapshot-if-stale a healthy
                       but stale state is snapshotted first (self-healing, no scheduler needed).
  restore DEST         restore the newest snapshot into an EMPTY folder (never overwrites).
  drill [--no-decrypt] restore into a scratch folder, hash-verify, decrypt every .age, compare with
                       live state, then discard; fails loudly on any problem.
  mirror-gfs           copy the in-repo GFS catalog (backups/) to ~/.artha-local/gfs-mirror (no deletes).
  status               one-line summary.

Backup root: $ARTHA_STATE_BACKUP_DIR or ~/.artha-local/state-backups (outside the checkout, 0700).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from lib.state_writer import write_atomic

ARTHA_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = ARTHA_DIR / "state"
SHRINK_LIMIT = 0.20          # refuse/alarm when live file count drops by more than this
DEFAULT_MAX_AGE_HOURS = 48
KEEP_SNAPSHOTS = 30
_INCLUDE_SUFFIXES = {".md", ".age", ".yaml", ".yml", ".json", ".txt", ".csv"}
_EXCLUDE_NAMES = {"telemetry.jsonl", "checkpoint.json", ".DS_Store", ".gitkeep"}
_EXCLUDE_PREFIXES = ("skills_cache.json",)
_EXCLUDE_DIRS = {"traces", "connectors", "templates", "__pycache__"}   # regenerated, or tracked in git
MAX_FILE_BYTES = 3 * 1024 * 1024    # keeps decks, images and other large work files out of every snapshot


def backup_root() -> Path:
    env = os.environ.get("ARTHA_STATE_BACKUP_DIR")
    return Path(env) if env else Path.home() / ".artha-local" / "state-backups"


def _state_files(state_dir: Path) -> list[Path]:
    """State Files at any depth: text-type, at most MAX_FILE_BYTES, outside excluded folders."""
    if not state_dir.is_dir():
        return []
    out = []
    for p in sorted(state_dir.rglob("*")):
        rel = p.relative_to(state_dir)
        if not p.is_file() or p.name in _EXCLUDE_NAMES or p.name.startswith(_EXCLUDE_PREFIXES):
            continue
        if _EXCLUDE_DIRS & set(rel.parts[:-1]) or p.suffix not in _INCLUDE_SUFFIXES:
            continue
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        out.append(p)
    return out


def _rel(p: Path, base: Path) -> str:
    return p.relative_to(base).as_posix()


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_snapshots(root: Path | None = None) -> list[Path]:
    root = root or backup_root()
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "manifest.json").is_file())


def _manifest(snap: Path) -> dict:
    return json.loads((snap / "manifest.json").read_text(encoding="utf-8"))


def _age_hours(manifest: dict, now: datetime | None = None) -> float:
    created = datetime.fromisoformat(manifest["created"])
    return ((now or datetime.now(timezone.utc)) - created).total_seconds() / 3600


def snapshot(state_dir: Path = STATE_DIR, *, force: bool = False) -> tuple[int, str]:
    files = _state_files(state_dir)
    if not files:
        return 3, "no State Files found - nothing to snapshot (is state/ wiped?)"
    snaps = list_snapshots()
    if snaps and not force:
        prev = _manifest(snaps[-1])
        prev_n = len(prev["files"])
        if prev_n and len(files) < prev_n * (1 - SHRINK_LIMIT):
            return 3, (f"REFUSED: state has {len(files)} files, newest snapshot has {prev_n} "
                       f"(lost >{int(SHRINK_LIMIT * 100)}%). Investigate; use --force only if intended.")
    root = backup_root()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    tmp = root / f".tmp-{stamp}"
    tmp.mkdir(mode=0o700)
    entries = {}
    try:
        for p in files:
            rel = _rel(p, state_dir)
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
            os.chmod(dest, 0o600)
            entries[rel] = {"sha256": _sha256(dest), "size": dest.stat().st_size}
        write_atomic(tmp / "manifest.json", json.dumps({
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": str(state_dir), "files": entries,
        }, indent=1))
        final = root / stamp
        tmp.rename(final)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    for old in list_snapshots(root)[:-KEEP_SNAPSHOTS]:
        shutil.rmtree(old, ignore_errors=True)
    return 0, f"snapshot {final.name}: {len(entries)} files -> {root}"


def _decrypt_ok(path: Path) -> tuple[bool, str]:
    sys.path.insert(0, str(ARTHA_DIR / "scripts"))
    try:
        import foundation as f
        key, age = f.get_private_key(), f.get_age_bin()
    except BaseException as exc:  # SystemExit from a missing key must not kill the caller
        return False, f"key unavailable ({type(exc).__name__})"
    r = subprocess.run([age, "--decrypt", "--identity", "-", str(path)],
                       input=(key.rstrip() + "\n").encode(), capture_output=True, timeout=60)
    if r.returncode != 0:
        return False, "decrypt failed"
    # Only domain Markdown carries YAML frontmatter; the audit log and YAML stores do not.
    needs_frontmatter = path.name.endswith(".md.age") and not path.name.startswith("audit.")
    if needs_frontmatter and not r.stdout.startswith(b"---"):
        return False, "no frontmatter"
    return True, "ok"


def verify(*, decrypt: bool = True) -> tuple[int, str]:
    snaps = list_snapshots()
    if not snaps:
        return 1, "NO SNAPSHOT to verify"
    snap = snaps[-1]
    man, bad = _manifest(snap), []
    for name, meta in man["files"].items():
        p = snap / name
        if not p.is_file() or _sha256(p) != meta["sha256"]:
            bad.append(f"{name}: hash mismatch or missing")
        elif decrypt and name.endswith(".age"):
            ok, why = _decrypt_ok(p)
            if not ok:
                bad.append(f"{name}: {why}")
    if bad:
        return 1, f"VERIFY FAILED for {snap.name}: " + "; ".join(bad[:5])
    return 0, f"verified {snap.name}: {len(man['files'])} files" + (" (hashes + decrypt)" if decrypt else " (hashes)")


def restore(dest: Path, *, snapshot_name: str | None = None) -> tuple[int, str]:
    """Copy a snapshot (default: newest) into an EMPTY destination folder, verifying every hash."""
    snaps = list_snapshots()
    snap = next((s for s in snaps if s.name == snapshot_name), None) if snapshot_name else (snaps[-1] if snaps else None)
    if snap is None:
        return 1, "NO SNAPSHOT to restore"
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        return 1, f"REFUSED: {dest} is not empty; restore never overwrites existing files"
    dest.mkdir(parents=True, exist_ok=True)
    man, bad = _manifest(snap), []
    for name, meta in man["files"].items():
        (dest / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snap / name, dest / name)
        if _sha256(dest / name) != meta["sha256"]:
            bad.append(name)
    if bad:
        return 1, f"RESTORE FAILED hash check for: {', '.join(bad[:5])}"
    return 0, f"restored {len(man['files'])} files from {snap.name} to {dest}"


def drill(state_dir: Path = STATE_DIR, *, decrypt: bool = True) -> tuple[int, str]:
    """Restore the newest snapshot into a scratch folder, verify it, and compare it with live state."""
    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="artha-restore-drill-"))
    try:
        code, msg = restore(scratch / "restored")
        if code:
            return 1, "🔴 RESTORE DRILL FAILED: " + msg
        restored = scratch / "restored"
        files = sorted(p for p in restored.rglob("*") if p.is_file())
        bad = []
        if decrypt:
            for p in files:
                if p.name.endswith(".age"):
                    ok, why = _decrypt_ok(p)
                    if not ok:
                        bad.append(f"{_rel(p, restored)}: {why}")
        if bad:
            return 1, "🔴 RESTORE DRILL FAILED: " + "; ".join(bad[:5])
        live = {_rel(p, state_dir): p for p in _state_files(state_dir)}
        rels = {p: _rel(p, restored) for p in files}
        changed = sorted(r for p, r in rels.items() if r in live and _sha256(p) != _sha256(live[r]))
        gone = sorted(r for r in rels.values() if r not in live)
        note = f"; {len(changed)} changed and {len(gone)} absent in live state since the snapshot"
        return 0, f"restore drill OK: {len(files)} files restored and verified{' (decrypted)' if decrypt else ''}{note}"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def mirror_gfs(dest: Path | None = None) -> tuple[int, str]:
    """Copy the in-repo GFS catalog (backups/) to an off-repo mirror. Never deletes from the mirror."""
    src = ARTHA_DIR / "backups"
    dest = dest or (backup_root().parent / "gfs-mirror")
    if not src.is_dir():
        return 1, "🔴 no backups/ catalog to mirror"
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    copied = kept = 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for p in src.rglob("*"):
        if p.is_file() and not p.name.endswith((".lock", ".tmp")):
            target = dest / p.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.stat().st_size == p.stat().st_size and _sha256(target) == _sha256(p):
                    continue
                # Same name, new content (today's daily zip is rewritten each run): keep the old copy too.
                target = target.with_name(f"{target.stem}.{stamp}{target.suffix}")
                kept += 1
            shutil.copy2(p, target)
            copied += 1
    extra = f" ({kept} changed files kept as timestamped copies)" if kept else ""
    return 0, f"gfs mirror: {copied} new files copied to {dest}{extra}"


def check(state_dir: Path = STATE_DIR, *, max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
          snapshot_if_stale: bool = False) -> tuple[int, str]:
    snaps = list_snapshots()
    live = _state_files(state_dir)
    if not snaps:
        if snapshot_if_stale and live:
            code, msg = snapshot(state_dir)
            return code, ("first snapshot taken: " + msg) if code == 0 else ("NO BACKUP and snapshot failed: " + msg)
        return 1, "🔴 NO STATE BACKUP EXISTS - run: python3 scripts/state_guard.py snapshot"
    man = _manifest(snaps[-1])
    prev_n = len(man["files"])
    if prev_n and len(live) < prev_n * (1 - SHRINK_LIMIT):
        missing = sorted(set(man["files"]) - {_rel(p, state_dir) for p in live})
        return 1, (f"🔴 STATE SHRANK: {len(live)} files now vs {prev_n} in snapshot {snaps[-1].name}; "
                   f"missing e.g. {', '.join(missing[:6])}. Do NOT proceed; restore from "
                   f"{snaps[-1]}")
    age = _age_hours(man)
    if age > max_age_hours:
        if snapshot_if_stale:
            code, msg = snapshot(state_dir)
            return code, ("refreshed stale snapshot: " + msg) if code == 0 else ("🔴 snapshot failed: " + msg)
        return 1, f"🔴 NO STATE BACKUP SINCE {man['created']} ({age:.0f}h old, limit {max_age_hours:.0f}h)"
    return 0, f"state backup fresh: {snaps[-1].name} ({age:.1f}h old, {prev_n} files)"


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "snapshot":
        code, msg = snapshot(force="--force" in argv)
    elif cmd == "verify":
        code, msg = verify(decrypt="--no-decrypt" not in argv)
    elif cmd == "check":
        hours = DEFAULT_MAX_AGE_HOURS
        if "--max-age-hours" in argv:
            hours = float(argv[argv.index("--max-age-hours") + 1])
        code, msg = check(max_age_hours=hours, snapshot_if_stale="--snapshot-if-stale" in argv)
    elif cmd == "drill":
        code, msg = drill(decrypt="--no-decrypt" not in argv)
    elif cmd == "restore":
        if len(argv) < 2:
            print("usage: state_guard.py restore EMPTY_DEST_DIR")
            return 2
        code, msg = restore(Path(argv[1]))
    elif cmd == "mirror-gfs":
        code, msg = mirror_gfs()
    elif cmd == "status":
        code, msg = check()
    else:
        print(__doc__)
        return 2
    print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
