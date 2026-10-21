"""Mapping of the existing action database onto the hardened lifecycle (re-artha §7.8, RA-05).

Read-only planning for the coordinated schema/executor/queue migration: every legacy
status is mapped to a target status, and rows whose *effect may be uncertain* are
flagged instead of being trusted.  Nothing here writes to a database.
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from catchup_core.actions import ActionStatus

__all__ = ["LEGACY_MAP", "MappedRow", "MigrationPlan", "audit_database", "plan_migration"]

LEGACY_MAP: dict[str, ActionStatus] = {
    "pending": ActionStatus.PROPOSED,
    "modifying": ActionStatus.DRAFT,
    "deferred": ActionStatus.DEFERRED,
    "approved": ActionStatus.APPROVED,
    "executing": ActionStatus.UNKNOWN,        # may have reached the provider: never assume otherwise
    "unknown": ActionStatus.UNKNOWN,
    "succeeded": ActionStatus.SUCCEEDED,
    "failed": ActionStatus.FAILED,
    "rejected": ActionStatus.REJECTED,
    "expired": ActionStatus.EXPIRED,
    "cancelled": ActionStatus.CANCELLED,
}
_AMBIGUOUS_FAILURE = re.compile(r"(?i)time[d ]?-?out|timed out|no response|connection (reset|lost)|unknown|"
                                r"could not (verify|confirm)|partial")


@dataclass(frozen=True)
class MappedRow:
    action_id: str
    legacy_status: str
    target: ActionStatus
    needs_review: bool
    reason: str = ""


@dataclass(frozen=True)
class MigrationPlan:
    rows: tuple[MappedRow, ...]
    unmapped: tuple[str, ...]

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(r.target.value for r in self.rows))

    @property
    def review(self) -> tuple[MappedRow, ...]:
        return tuple(r for r in self.rows if r.needs_review)

    @property
    def safe_to_migrate(self) -> bool:
        return not self.unmapped


def plan_migration(rows: Iterable[Mapping[str, object]]) -> MigrationPlan:
    mapped: list[MappedRow] = []
    unmapped: list[str] = []
    for row in rows:
        rid = str(row.get("id", ""))
        legacy = str(row.get("status", ""))
        target = LEGACY_MAP.get(legacy)
        if target is None:
            unmapped.append(f"{rid}:{legacy}")
            continue
        review, reason = False, ""
        if legacy == "executing":
            review, reason = True, "was in flight; effect may have happened"
        elif legacy == "failed":
            text = f"{row.get('result_message') or ''} {row.get('result_status') or ''}"
            if row.get("result_status") == "partial" or _AMBIGUOUS_FAILURE.search(text):
                target, review, reason = ActionStatus.UNKNOWN, True, "failure text does not prove no effect"
        elif legacy == "approved":
            review, reason = True, "approval predates payload binding; re-approve the exact preview"
        mapped.append(MappedRow(rid, legacy, target, review, reason))
    return MigrationPlan(tuple(mapped), tuple(unmapped))


def audit_database(path: Path | str) -> MigrationPlan:
    """Open the action database read-only and plan its migration."""
    uri = f"file:{Path(path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute("SELECT id, status, result_status, result_message FROM actions")]
    finally:
        conn.close()
    return plan_migration(rows)
