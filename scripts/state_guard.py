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
_INCLUDE_SUFFIXES = {".md", ".age", ".yaml", ".yml", ".json"}
_EXCLUDE_NAMES = {"telemetry.jsonl", "checkpoint.json", ".DS_Store", ".gitkeep"}
_EXCLUDE_PREFIXES = ("skills_cache.json",)


def backup_root() -> Path:
    env = os.environ.get("ARTHA_STATE_BACKUP_DIR")
    return Path(env) if env else Path.home() / ".artha-local" / "state-backups"


def _state_files(state_dir: Path) -> list[Path]:
    if not state_dir.is_dir():
        return []
    out = []
    for p in sorted(state_dir.iterdir()):
        if not p.is_file() or p.name in _EXCLUDE_NAMES or p.name.startswith(_EXCLUDE_PREFIXES):
            continue
        if p.suffix in _INCLUDE_SUFFIXES:
            out.append(p)
    return out


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
            dest = tmp / p.name
            shutil.copy2(p, dest)
            os.chmod(dest, 0o600)
            entries[p.name] = {"sha256": _sha256(dest), "size": dest.stat().st_size}
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
    return (r.stdout.startswith(b"---"), "ok" if r.stdout.startswith(b"---") else "no frontmatter")


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
        missing = sorted(set(man["files"]) - {p.name for p in live})
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
    elif cmd == "status":
        code, msg = check()
    else:
        print(__doc__)
        return 2
    print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
