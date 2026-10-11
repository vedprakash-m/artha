#!/usr/bin/env python3
"""Ratcheted ban on direct ``.write_text()`` calls outside the state writers.

The legacy check (a ``grep`` in the Makefile) has been red for a long time
because the incumbent code base contains ~160 direct writes.  A permanently
red check protects nothing, so this tool records the reviewed per-file counts
in ``tools/baselines/state_writes.json`` and fails when

* any file gains a direct write, or
* a file that was not in the baseline gains any.

Counts may only go down.  Lowering a count is done with ``--update-baseline``
in the same change that removes the write.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ALLOWED_FILES = {
    "scripts/lib/state_writer.py",
    "scripts/lib/health_check_writer.py",
}
PATTERN = re.compile(r"\.write_text\(")
TMP_ON_LINE = re.compile(r"\.write_text.*tmp/")
BASELINE = Path(__file__).resolve().parent / "baselines" / "state_writes.json"


def scan(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    base = root / "scripts"
    if not base.is_dir():
        return counts
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        parts = set(path.parts)
        if "__pycache__" in parts or ".archive" in parts:
            continue
        if rel in ALLOWED_FILES or path.name.startswith("test_"):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        hits = sum(1 for ln in lines if PATTERN.search(ln) and not TMP_ON_LINE.search(ln))
        if hits:
            counts[rel] = hits
    return counts


def compare(current: dict[str, int], baseline: dict[str, int]) -> tuple[list[str], list[str]]:
    regressions = [
        f"{rel}: {n} direct write(s), baseline {baseline.get(rel, 0)}"
        for rel, n in sorted(current.items())
        if n > baseline.get(rel, 0)
    ]
    improvements = [
        f"{rel}: {baseline[rel]} -> {current.get(rel, 0)}"
        for rel in sorted(baseline)
        if current.get(rel, 0) < baseline[rel]
    ]
    return regressions, improvements


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 and not argv[1].startswith("--") else Path.cwd()
    baseline_path = BASELINE
    current = scan(root)
    if "--update-baseline" in argv:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"baseline written: {sum(current.values())} direct writes in {len(current)} files")
        return 0
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot read baseline {baseline_path}: {exc}")
        return 1
    regressions, improvements = compare(current, baseline)
    if regressions:
        print("FAIL: new direct .write_text() calls — route state writes through "
              "state_writer.write(), tmp writes through state_writer.write_atomic():")
        print("\n".join(f"  {r}" for r in regressions))
        return 1
    if improvements:
        print("note: counts dropped — run `python tools/lint_state_writes.py --update-baseline` to ratchet:")
        print("\n".join(f"  {i}" for i in improvements))
    print(f"✓ No new direct .write_text() calls ({sum(current.values())} legacy, baseline {sum(baseline.values())})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
