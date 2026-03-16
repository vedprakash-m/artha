#!/usr/bin/env python3
"""
diff_view.py — Session state diff for catch-up close
====================================================
Compares state files before and after a catch-up to show what changed.
Provides accountability ("AI said it updated X — here's the proof").

Usage:
  # At catch-up start (Step 1): snapshot current checksums
  python scripts/diff_view.py --snapshot

  # At catch-up close (Step 18b): show what changed
  python scripts/diff_view.py --since-session

  # Compare against N days ago (uses git)
  python scripts/diff_view.py --since 7d

Exit codes:
  0 — success (diff shown or snapshot taken)
  1 — no snapshot found (diff --since-session cannot run)
  2 — error

Output format (--since-session):
  ━━ SESSION CHANGES ━━━━━━━━━━━━━━━━━━━━━━━
    state/finance.md       +3 lines  (bill due date added)
    state/calendar.md      +2 lines
    state/open_items.md    modified  (content changed)
    ─────────────────────────────────────────
    5 files checked · 3 modified · 2 unchanged
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Ref: specs/enhance.md §10.0.4
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent
_STATE_DIR = _ARTHA_DIR / "state"
_TMP_DIR = _ARTHA_DIR / "tmp"

_CHECKPOINT_GLOB = ".catchup_*_checksums.json"
_SENSITIVE_DOMAINS = {
    "immigration", "finance", "insurance", "estate",
    "health", "audit", "vehicle", "contacts", "occasions",
}


def _sha256(path: Path) -> str:
    """Compute SHA-256 of file. Returns empty string if file missing."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _linecount(path: Path) -> int:
    """Count lines in file. Returns 0 if missing."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _snapshot_state_files() -> dict[str, dict]:
    """Build a snapshot dict: {relative_path: {sha256, linecount}}."""
    snapshot: dict[str, dict] = {}
    for path in sorted(_STATE_DIR.glob("*.md")):
        rel = str(path.relative_to(_ARTHA_DIR))
        snapshot[rel] = {
            "sha256": _sha256(path),
            "linecount": _linecount(path),
        }
    # Also track .age files (to detect encrypt/decrypt changes)
    for path in sorted(_STATE_DIR.glob("*.md.age")):
        rel = str(path.relative_to(_ARTHA_DIR))
        snapshot[rel] = {
            "sha256": _sha256(path),
            "linecount": 0,  # Binary — line count meaningless
        }
    return snapshot


def _checkpoint_path() -> Path:
    """Return path for the current session's checkpoint file (timestamped)."""
    _TMP_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return _TMP_DIR / f".catchup_{ts}_checksums.json"


def _find_latest_checkpoint() -> Path | None:
    """Find the most recent checkpoint file in tmp/."""
    _TMP_DIR.mkdir(exist_ok=True)
    checkpoints = sorted(_TMP_DIR.glob(".catchup_*_checksums.json"))
    return checkpoints[-1] if checkpoints else None


def do_snapshot() -> int:
    """Take a checkpoint of current state file checksums."""
    _TMP_DIR.mkdir(exist_ok=True)

    # Warn if a previous (possibly stale) checkpoint exists
    existing = sorted(_TMP_DIR.glob(".catchup_*_checksums.json"))
    if existing:
        print(f"⚠ Found previous checkpoint (possibly stale from crashed session): {existing[-1].name}")
        print("  This checkpoint is preserved. A fresh checkpoint will be created.")

    snapshot = _snapshot_state_files()
    checkpoint = _checkpoint_path()
    checkpoint.write_text(
        json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "files": snapshot}, indent=2),
        encoding="utf-8",
    )
    print(f"✓ Session checkpoint created: {checkpoint.name} ({len(snapshot)} files tracked)")
    return 0


def do_since_session() -> int:
    """Show what changed from the session checkpoint to now."""
    checkpoint = _find_latest_checkpoint()
    if checkpoint is None:
        print("⚠ No session checkpoint found. Run `diff_view.py --snapshot` at catch-up start.", file=sys.stderr)
        return 1

    try:
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: Cannot read checkpoint {checkpoint}: {e}", file=sys.stderr)
        return 2

    before = data.get("files", {})
    after = _snapshot_state_files()
    ts = data.get("timestamp", "unknown")

    # Compute diff
    all_files = sorted(set(list(before.keys()) + list(after.keys())))
    modified: list[dict] = []
    unchanged: list[str] = []
    added: list[str] = []
    removed: list[str] = []

    for rel in all_files:
        # Skip sensitive domain content — show only metadata (file name + line delta)
        domain = Path(rel).stem.replace(".md", "")
        is_sensitive = domain in _SENSITIVE_DOMAINS

        b = before.get(rel)
        a_now = after.get(rel)

        if b is None and a_now is not None:
            added.append(rel)
        elif b is not None and a_now is None:
            removed.append(rel)
        elif b["sha256"] != a_now["sha256"]:
            line_delta = a_now["linecount"] - b["linecount"]
            delta_str = f"+{line_delta}" if line_delta >= 0 else str(line_delta)
            modified.append({
                "path": rel,
                "line_delta": delta_str,
                "is_sensitive": is_sensitive,
            })
        else:
            unchanged.append(rel)

    # Format output
    total = len(all_files)
    mod_count = len(modified) + len(added) + len(removed)

    print("━━ SESSION CHANGES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if modified:
        for item in modified:
            delta = item["line_delta"]
            label = "lines" if delta != "0" and delta != "+0" else "modified"
            sensitive_note = "  [content redacted — sensitive domain]" if item["is_sensitive"] else ""
            print(f"  {item['path']:<35} {delta:>6} {label}{sensitive_note}")
    if added:
        for path in added:
            print(f"  {path:<35}    new file added")
    if removed:
        for path in removed:
            print(f"  {path:<35}    removed")

    if not modified and not added and not removed:
        print("  No state files were modified this session.")

    print("  ─────────────────────────────────────────────────────")
    print(f"  {total} files checked · {mod_count} modified · {len(unchanged)} unchanged")
    print(f"  Session started: {ts}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Zero-modification warning: if a catch-up claimed to process emails but
    # nothing changed, that's suspicious
    if mod_count == 0:
        print("\n⚠ WARNING: No state files were modified this session.")
        print("  If emails were processed, verify domain routing is working correctly.")

    # Clean up the checkpoint on success
    try:
        checkpoint.unlink()
    except OSError:
        pass  # Non-fatal

    return 0


def do_since_days(days: int) -> int:
    """Show changes over the last N days (uses git log if available)."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "-C", str(_ARTHA_DIR), "log", f"--since={days} days ago",
             "--name-status", "--format=", "--", "state/*.md"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            print(f"⚠ Git unavailable ({result.stderr.strip()[:100]}). Try --since-session instead.", file=sys.stderr)
            return 1
        output = result.stdout.strip()
        if not output:
            print(f"No state file changes in the last {days} days (per git log).")
        else:
            print(f"State changes in last {days} days:\n")
            print(output)
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Artha Session State Diff")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", action="store_true", help="Snapshot current state (call at catch-up start)")
    group.add_argument("--since-session", action="store_true", help="Show changes since session start")
    group.add_argument("--since", metavar="SPEC", help="Show changes since N days (e.g. 7d)")
    args = parser.parse_args()

    if args.snapshot:
        return do_snapshot()
    elif args.since_session:
        return do_since_session()
    elif args.since:
        # Parse "7d" → 7
        spec = args.since.rstrip("d").strip()
        try:
            days = int(spec)
        except ValueError:
            print(f"ERROR: Invalid --since value '{args.since}'. Use e.g. '7d'.", file=sys.stderr)
            return 2
        return do_since_days(days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
