#!/usr/bin/env python3
"""Warn when changes touch files that the signed slice release fingerprints.

The authoritative Catch-up refuses to run once the implementation fingerprint differs from the
signed cohort (re-artha re-sign 17 was forced by connector/preflight edits nobody flagged).
This check never blocks: it names the fingerprinted files in the change so the author knows a
re-sign (or a revert) is required before the next authoritative run.

    python tools/check_release_drift.py --staged          # pre-commit
    python tools/check_release_drift.py --files a.py b.py # explicit list
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fingerprinted_paths(root: Path = ROOT) -> set[str]:
    """Mirror of ``catchup_core.runtime._implementation_fingerprint`` path selection."""
    sys.path.insert(0, str(root / "scripts"))
    try:
        from catchup_core.capabilities import CAPABILITIES  # noqa: PLC0415
    finally:
        sys.path.pop(0)
    paths = {path for capability in CAPABILITIES for path in capability.modules if path.startswith("scripts/")}
    paths.update(p.relative_to(root).as_posix() for p in (root / "scripts/catchup_core").glob("*.py"))
    paths.update(p.relative_to(root).as_posix() for p in (root / "scripts/catchup_core/migrations").glob("*.sql"))
    return paths


def drift(changed: list[str], root: Path = ROOT) -> list[str]:
    selected = fingerprinted_paths(root)
    return sorted(path for path in changed if path in selected
                  or (path.startswith("scripts/catchup_core/") and path.endswith((".py", ".sql"))
                      and path.count("/") in (2, 3)))


def _staged(root: Path) -> list[str]:
    result = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--name-only", "--diff-filter=ACMRD"],
                            capture_output=True, text=True, check=False)
    return [line for line in result.stdout.splitlines() if line]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true")
    group.add_argument("--files", nargs="+")
    args = parser.parse_args(argv)
    try:
        touched = drift(_staged(ROOT) if args.staged else args.files)
    except Exception as exc:  # noqa: BLE001 - advisory only; never block a commit on this check
        print(f"[release-drift] could not evaluate: {type(exc).__name__}", file=sys.stderr)
        return 0
    if touched:
        print("[release-drift] This change touches files the signed slice release fingerprints:\n  "
              + "\n  ".join(touched)
              + "\n  Authoritative Catch-ups will refuse until the owner re-signs (or the change is reverted).",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
