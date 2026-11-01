#!/usr/bin/env python3
"""scripts/pipeline_audit.py — F-D2 citation sentinel audit.

Counts [src: ...] tokens and {{MISSING: ...}} sentinels in a briefing.
Validates OI-NNN IDs against state/open_items.md.
Writes one-line summary to state/audit.md (appended, never overwritten).

Usage:
    python scripts/pipeline_audit.py briefings/YYYY-MM-DD.md
    python scripts/pipeline_audit.py --last    # most recent full briefing
    python scripts/pipeline_audit.py --all     # last 5 briefings, rolling view

Gate schedule (F-D2 spec):
    Days 0-14: warning-only (audit runs but never blocks)
    Day 15+:   soft-blocking if 5-briefing rolling compliance >= 80%

Ref: specs/re-artha.md §F-D2
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_OPEN_ITEMS = _ROOT / "state" / "open_items.md"
_AUDIT_FILE = _ROOT / "state" / "audit.md"

# Citation token patterns — only these three forms are valid
_RX_SRC_OI = re.compile(r'\[src:\s*OI-(\d+)\]')
_RX_SRC_STATE = re.compile(r'\[src:\s*state:([a-z_]+)\]')
_RX_SRC_SIGNAL = re.compile(r'\[src:\s*signal:SIG-(\d+)\]')
_RX_MISSING = re.compile(r'\{\{MISSING:[^}]+\}\}')
_RX_ANY_SRC = re.compile(r'\[src:[^\]]+\]')
# Grammar violation: [src:...] that doesn't match any valid form
_RX_BAD_SRC = re.compile(
    r'\[src:(?!\s*(?:OI-\d+|state:[a-z_]+|signal:SIG-\d+))[^\]]+\]'
)
# Factual verbs heuristic — lines with these verbs and no [src:] are flagged
_RX_FACT_VERB = re.compile(r'\b(is|has|will|was|are|have|were)\b', re.IGNORECASE)


def _valid_oi_ids() -> set[str]:
    if not _OPEN_ITEMS.exists():
        return set()
    return {
        m.group(1)
        for line in _OPEN_ITEMS.read_text(encoding="utf-8").splitlines()
        if (m := re.match(r'\s*-\s*id:\s*(OI-\d+)', line))
    }


def audit(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    oi_nums = _RX_SRC_OI.findall(text)
    state_domains = _RX_SRC_STATE.findall(text)
    signal_nums = _RX_SRC_SIGNAL.findall(text)
    missing = _RX_MISSING.findall(text)
    bad = _RX_BAD_SRC.findall(text)

    # Verify OI-NNN tokens resolve to real IDs
    valid_ids = _valid_oi_ids()
    invalid_oi = [f"OI-{n}" for n in oi_nums if f"OI-{n}" not in valid_ids]

    # Lines with factual verbs but no [src:] — skip headers/code/bullets
    skip_prefix = ("#", "-", "|", "```", "---", ">", "  ", "\t")
    uncited = [
        i
        for i, ln in enumerate(lines, 1)
        if not ln.strip().startswith(skip_prefix)
        and _RX_FACT_VERB.search(ln)
        and not _RX_ANY_SRC.search(ln)
    ]

    return {
        "file": path.name,
        "oi": len(oi_nums),
        "state": len(state_domains),
        "signal": len(signal_nums),
        "missing": len(missing),
        "bad": bad,
        "invalid_oi": invalid_oi,
        "uncited": len(uncited),
        "total_src": len(oi_nums) + len(state_domains) + len(signal_nums),
    }


def _append_audit(r: dict) -> None:
    today = date.today().isoformat()
    summary = (
        f"{today} | {r['file']} | "
        f"src={r['total_src']} (OI={r['oi']} state={r['state']} sig={r['signal']}) | "
        f"missing={r['missing']} | uncited={r['uncited']} | "
        f"bad={len(r['bad'])} | invalid_oi={len(r['invalid_oi'])}\n"
    )
    with open(_AUDIT_FILE, "a", encoding="utf-8") as fh:
        fh.write(summary)


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    if args[0] == "--last":
        candidates = sorted(
            _ROOT.glob("briefings/*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        targets = [p for p in candidates if "digest" not in p.name][:1]
    elif args[0] == "--all":
        candidates = sorted(
            _ROOT.glob("briefings/*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        targets = [p for p in candidates if "digest" not in p.name][:5]
    else:
        targets = [Path(a) for a in args]

    rc = 0
    for target in targets:
        if not target.exists():
            print(f"[audit] ERROR: {target} not found", file=sys.stderr)
            rc = 1
            continue
        r = audit(target)
        _append_audit(r)
        flag = "✓" if not (r["uncited"] or r["bad"] or r["invalid_oi"]) else "⚠"
        extras = ""
        if r["invalid_oi"]:
            extras += f" INVALID_OI={r['invalid_oi']}"
        if r["bad"]:
            extras += f" BAD={r['bad'][:2]}"
        print(
            f"{flag} {r['file']}: src={r['total_src']} "
            f"missing={r['missing']} uncited={r['uncited']}{extras}"
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
