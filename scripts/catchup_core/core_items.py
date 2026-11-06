"""Core-owned Open Items file for released Domains (.archive/specs/core-open-items-2026-11-05.md)."""
from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from catchup_core.blobs import private_atomic_bytes
from catchup_core.triage import AlertDraft

__all__ = ["publish", "render"]

_HEADER = ("# Core Open Items\n\n"
           "This file is core-owned: the Catch-up core regenerated it on its latest authoritative run.\n"
           "Do not edit it. It lists open obligations for the released Domains;\n"
           "the incumbent no longer records these Domains in `open_items.md`.\n\n")


def _cell(value: object) -> str:
    return str(value).replace("|", "/").replace("\n", " ").strip()


def render(alerts: Sequence[AlertDraft], labels: Mapping[str, str]) -> str:
    if not alerts:
        return _HEADER + "None open.\n"
    unique = {a.obligation_key: a for a in alerts}.values()
    rows = sorted(unique, key=lambda a: (a.deadline is None, a.deadline or "", a.obligation_key))
    lines = ["| Domain | Who | Obligation | Due | Days | Severity | State | Key |", "|---|---|---|---|---|---|---|---|"]
    for a in rows:
        lines.append("| " + " | ".join(_cell(x) for x in (
            a.domain, labels.get(a.subject_id, a.subject_id), a.predicate.replace("_", " "),
            a.deadline.isoformat() if a.deadline else "", "" if a.days_until is None else a.days_until,
            a.severity.value if a.severity else "unscored", a.state, a.obligation_key)) + " |")
    return _HEADER + "\n".join(lines) + "\n"


def publish(path: Path, text: str) -> bool:
    """Write atomically; return False and leave the file untouched when the bytes are already current."""
    data = text.encode()
    if path.is_file() and path.read_bytes() == data:
        return False
    try:
        private_atomic_bytes(path, data)
    except RuntimeError as error:
        # A file written before the private-DACL change inherits its folder's DACL. The core regenerates the
        # whole file every run, so remove that legacy copy once and publish privately.
        if sys.platform != "win32" or "owner-private" not in str(error) or not path.is_file():
            raise
        path.unlink()
        private_atomic_bytes(path, data)
    return True
