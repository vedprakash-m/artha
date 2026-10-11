#!/usr/bin/env python3
"""
scripts/lib/open_items_lint.py — schema-aware linter and repair for open_items.md
================================================================================
re-artha §12.2 item 8 (WP-03).  ``state/open_items.md`` is a curated file written
by several paths; 57 items had ``date_added:`` at column 0, which is invalid
YAML and made the field invisible to every reader.

* ``lint(text)``   — structural and value issues, with line numbers.
* ``repair(text)`` — the one mechanical repair that is always safe: re-indent a
  known item field that lost its two-space indent.  It never invents a missing
  value and never edits descriptions.
* CLI: ``--check`` (exit 1 on issues) and ``--fix`` (writes through
  ``lib.state_writer``, which snapshots the previous version).

Stdlib only so preflight can import it.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REQUIRED = ("id", "source_domain", "description", "priority", "status")
KNOWN_FIELDS = {
    "id", "date_added", "source_domain", "description", "deadline", "priority", "status",
    "origin", "last_seen", "last_seen_notes", "todo_id", "closed_date", "closed_notes",
    "source", "source_ref", "review_cadence", "planning_followup", "surfacing_status",
    "review_reason", "stale_notes", "via", "date_done", "resolved_at",
}
PRIORITIES = {"P0", "P1", "P2", "P3"}
STATUSES = {"open", "done", "closed", "deferred", "blocked", "not_proceeding", "expired", "in_progress"}
CLOSED_STATUSES = {"done", "closed", "not_proceeding", "expired"}
_DATE_FIELDS = ("date_added", "deadline", "last_seen", "closed_date")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ].*)?$")
_ITEM_START = re.compile(r"^- id:\s*(.*?)\s*$")
_FIELD_INDENTED = re.compile(r"^  ([A-Za-z_][\w-]*):\s?(.*)$")
_FIELD_COL0 = re.compile(r"^([A-Za-z_][\w-]*):(\s.*)?$")


#: Issues that are organisation hints rather than defects; they never fail --check.
WARNING_CODES = {"section-mismatch"}


@dataclass(frozen=True)
class Issue:
    line: int
    code: str
    message: str

    @property
    def is_error(self) -> bool:
        return self.code not in WARNING_CODES

    def render(self, path: str = "open_items.md") -> str:
        level = "error" if self.is_error else "warning"
        return f"{path}:{self.line}: {level}: {self.code}: {self.message}"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def lint(text: str) -> list[Issue]:
    issues: list[Issue] = []
    items: list[dict] = []
    current: dict | None = None
    section = ""
    seen_ids: dict[str, int] = {}

    for number, line in enumerate(text.split("\n"), 1):
        if line.startswith("## "):
            section = line[3:].strip().lower()
            current = None
            continue
        m = _ITEM_START.match(line)
        if m:
            current = {"_line": number, "_section": section, "id": _unquote(m.group(1)), "_fields": {"id": number}}
            items.append(current)
            continue
        if current is None:
            continue
        fm = _FIELD_INDENTED.match(line)
        if fm:
            key, value = fm.group(1), fm.group(2)
            current.setdefault(key, _unquote(value))
            current["_fields"].setdefault(key, number)
            continue
        cm = _FIELD_COL0.match(line)
        if cm and cm.group(1) in KNOWN_FIELDS and line.strip():
            issues.append(Issue(number, "unindented-field",
                                f"'{cm.group(1)}' must be indented two spaces under its '- id:' entry"))
            key, value = cm.group(1), (cm.group(2) or "").strip()
            current.setdefault(key, _unquote(value))
            current["_fields"].setdefault(key, number)

    for item in items:
        line = item["_line"]
        ident = item.get("id", "")
        if not re.fullmatch(r"OI-\d+", ident):
            issues.append(Issue(line, "bad-id", f"id {ident!r} does not match OI-<number>"))
        if ident in seen_ids:
            issues.append(Issue(line, "duplicate-id", f"{ident} already defined at line {seen_ids[ident]}"))
        else:
            seen_ids[ident] = line
        for field in REQUIRED:
            if not str(item.get(field, "")).strip():
                issues.append(Issue(line, "missing-required", f"{ident or '?'} has no '{field}'"))
        prio = item.get("priority", "")
        if prio and prio not in PRIORITIES:
            issues.append(Issue(item["_fields"].get("priority", line), "bad-priority",
                                f"priority {prio!r} not in {sorted(PRIORITIES)}"))
        status = item.get("status", "")
        if status and status not in STATUSES:
            issues.append(Issue(item["_fields"].get("status", line), "bad-status",
                                f"status {status!r} not in {sorted(STATUSES)}"))
        for field in _DATE_FIELDS:
            value = item.get(field, "")
            if value and not _DATE_RE.match(value):
                issues.append(Issue(item["_fields"].get(field, line), "bad-date",
                                    f"{field} {value!r} is not YYYY-MM-DD"))
        in_closed = item["_section"].startswith("closed")
        if status in CLOSED_STATUSES and not in_closed and item["_section"].startswith("active"):
            issues.append(Issue(line, "section-mismatch", f"{ident} is {status} but listed under Active"))
        if status == "open" and in_closed:
            issues.append(Issue(line, "section-mismatch", f"{ident} is open but listed under Closed"))
    return issues


@dataclass(frozen=True)
class Change:
    line: int
    description: str


def repair(text: str) -> tuple[str, list[Change]]:
    """Re-indent known item fields that lost their indent.  Nothing else changes."""
    out: list[str] = []
    changes: list[Change] = []
    in_item = False
    for number, line in enumerate(text.split("\n"), 1):
        if line.startswith("## "):
            in_item = False
        elif _ITEM_START.match(line):
            in_item = True
        elif in_item:
            cm = _FIELD_COL0.match(line)
            if cm and cm.group(1) in KNOWN_FIELDS and line.strip():
                out.append("  " + line)
                changes.append(Change(number, f"indented '{cm.group(1)}'"))
                continue
            if not line.strip():
                pass  # blank line inside a block: keep in_item (fields may follow a gap)
        out.append(line)
    return "\n".join(out), changes


def _default_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "state" / "open_items.md"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    path = _default_path()
    if "--path" in argv:
        path = Path(argv[argv.index("--path") + 1])
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 2

    if "--fix" in argv:
        fixed, changes = repair(text)
        if not changes:
            print("open_items.md: nothing to repair")
            return 0 if not any(i.is_error for i in lint(text)) else 1
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from lib.state_writer import write  # noqa: PLC0415
            result = write(path, fixed, domain="open_items", source="open_items_lint", pii_check=False)
            ok = bool(result.success)
        except Exception as exc:  # noqa: BLE001
            print(f"state writer unavailable ({exc}); not writing", file=sys.stderr)
            return 2
        if not ok:
            print("state writer refused the repair; nothing written", file=sys.stderr)
            return 2
        print(f"open_items.md: repaired {len(changes)} field(s) at lines "
              f"{', '.join(str(c.line) for c in changes[:20])}{' …' if len(changes) > 20 else ''}")
        remaining = lint(fixed)
        for issue in remaining:
            print(issue.render(str(path.name)))
        return 0 if not any(i.is_error for i in remaining) else 1

    issues = lint(text)
    for issue in issues:
        print(issue.render(str(path.name)))
    errors = [i for i in issues if i.is_error]
    if not issues:
        print("open_items.md: clean")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
