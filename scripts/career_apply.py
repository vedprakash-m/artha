"""scripts/career_apply.py — Record a career application submission.

Transitions a tracker row from `Evaluated` → `Applied` and records submission
metadata (timestamp, portal, referrer). Auto-closes the matching
sponsorship-verify open item when one exists. Emits a `career_apply` trace event.

All mutations are deterministic Python (no LLM calls). Uses write_state_atomic
for tracker + open_items writes.

Usage:
    python3 scripts/career_apply.py <NNN> [--portal greenhouse] [--referrer "R. Smith"] [--notes "extra"]

Example:
    python3 scripts/career_apply.py 001 --portal greenhouse --referrer "J. Doe"

Exits 0 on success, 1 if report not found / status not Evaluated / malformed tracker.

Ref: prompts/career_search.md §Post-Evaluation Protocol (Applied status semantics)
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.career_state import (
    _read_frontmatter,
    _read_body,
    _write_frontmatter,
    reconcile_summary,
    write_state_atomic,
)
from lib.career_trace import CareerTrace

log = logging.getLogger(__name__)

_STATE_FILE = _REPO_ROOT / "state" / "career_search.md"
_OPEN_ITEMS_FILE = _REPO_ROOT / "state" / "open_items.md"


def _find_row_index(body: str, report_num: str) -> Optional[int]:
    """Return line index of the tracker row with leading '| {report_num} |'."""
    lines = body.splitlines()
    prefix = f"| {int(report_num)} |"
    for idx, line in enumerate(lines):
        if line.strip().startswith(prefix):
            return idx
    return None


def _update_tracker_row(
    body: str,
    report_num: str,
    new_status: str,
    append_note: str,
) -> tuple[str, dict[str, str]]:
    """Rewrite a tracker row's status + notes. Returns (new_body, parsed_row_dict)."""
    idx = _find_row_index(body, report_num)
    if idx is None:
        raise ValueError(f"Report {report_num} not found in Applications tracker")

    lines = body.splitlines()
    line = lines[idx]
    parts = [p.strip() for p in line.strip().strip("|").split("|")]
    if len(parts) < 9:
        raise ValueError(f"Malformed tracker row for report {report_num}: {line!r}")

    row = {
        "num": parts[0], "date": parts[1], "company": parts[2], "role": parts[3],
        "score": parts[4], "status": parts[5], "pdf": parts[6],
        "report": parts[7], "notes": parts[8],
    }
    if row["status"] != "Evaluated":
        raise ValueError(
            f"Report {report_num} status is {row['status']!r}, expected 'Evaluated'. "
            "Apply can only transition Evaluated rows."
        )

    parts[5] = new_status
    merged_notes = parts[8]
    if append_note:
        merged_notes = f"{parts[8]} · {append_note}" if parts[8] else append_note
    parts[8] = merged_notes
    lines[idx] = "| " + " | ".join(parts) + " |"
    return "\n".join(lines) + ("\n" if body.endswith("\n") else ""), row


_OI_ENTRY_RE = re.compile(
    r"(- id: OI-\d+\n(?:  [^\n]*\n)*?)(?=\n- id: OI-\d+|\n## |\Z)",
    re.MULTILINE,
)


def _close_sponsorship_oi(company: str, role: str) -> Optional[str]:
    """Close an open sponsorship-verify OI matching {company} + {role} tokens.

    Returns the OI id that was closed, or None if no match.
    """
    if not _OPEN_ITEMS_FILE.exists():
        return None
    text = _OPEN_ITEMS_FILE.read_text(encoding="utf-8")
    today = date.today().isoformat()
    company_tok = company.strip()
    role_tok = role.strip()

    def is_open_sponsorship_match(block: str) -> bool:
        if "status: open" not in block:
            return False
        if "source_domain: career" not in block:
            return False
        if company_tok not in block or role_tok not in block:
            return False
        if "sponsorship" not in block.lower() and "H-1B" not in block:
            return False
        return True

    closed_id: Optional[str] = None
    new_text_parts: list[str] = []
    last_end = 0
    for m in _OI_ENTRY_RE.finditer(text):
        block = m.group(1)
        new_text_parts.append(text[last_end:m.start()])
        if closed_id is None and is_open_sponsorship_match(block):
            id_match = re.search(r"- id: (OI-\d+)", block)
            closed_id = id_match.group(1) if id_match else None
            block = re.sub(r"  status: open", "  status: done", block)
            if "  closed_date:" not in block:
                block = block.rstrip("\n") + f"\n  closed_date: {today}\n  closed_notes: \"Auto-closed by career_apply: application submitted.\"\n"
        new_text_parts.append(block)
        last_end = m.end()
    new_text_parts.append(text[last_end:])

    if closed_id:
        write_state_atomic(_OPEN_ITEMS_FILE, "".join(new_text_parts))
    return closed_id


def apply(
    report_num: str,
    portal: Optional[str] = None,
    referrer: Optional[str] = None,
    extra_notes: Optional[str] = None,
) -> dict:
    """Perform the full apply transition. Returns a result dict."""
    fm = _read_frontmatter(_STATE_FILE)
    body = _read_body(_STATE_FILE)
    report_num_padded = str(int(report_num)).zfill(3)

    bits: list[str] = [f"Applied {date.today().isoformat()}"]
    if portal:
        bits.append(f"via {portal}")
    if referrer:
        bits.append(f"ref: {referrer}")
    if extra_notes:
        bits.append(extra_notes)
    applied_note = " · ".join(bits)

    new_body, row = _update_tracker_row(body, report_num, "Applied", applied_note)

    summary = fm.get("summary") or {}
    summary["last_apply_date"] = date.today().isoformat()
    fm["summary"] = summary

    _write_frontmatter(_STATE_FILE, fm, new_body)
    reconcile_summary(_STATE_FILE)

    closed_oi = _close_sponsorship_oi(row["company"], row["role"])

    CareerTrace().write_apply_event(
        report_number=report_num_padded,
        company=row["company"],
        role=row["role"],
        portal=portal or "",
        referrer=referrer or "",
        closed_open_item=closed_oi or "",
    )

    return {
        "status": "success",
        "report_number": report_num_padded,
        "company": row["company"],
        "role": row["role"],
        "prior_status": row["status"],
        "new_status": "Applied",
        "closed_open_item": closed_oi,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a career application submission.")
    parser.add_argument("report_num", help="Report number (e.g. 001 or 1)")
    parser.add_argument("--portal", help="Portal used (greenhouse/ashby/lever/linkedin/direct)")
    parser.add_argument("--referrer", help="Referrer name (if any)")
    parser.add_argument("--notes", help="Extra notes to append to tracker row")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        result = apply(
            report_num=args.report_num,
            portal=args.portal,
            referrer=args.referrer,
            extra_notes=args.notes,
        )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"[OK] Report {result['report_number']} — {result['company']} · {result['role']}")
    print(f"     status: {result['prior_status']} → {result['new_status']}")
    if result["closed_open_item"]:
        print(f"     auto-closed open item: {result['closed_open_item']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
