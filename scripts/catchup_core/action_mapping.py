"""Mapping of the existing action database onto the hardened lifecycle (re-artha §7.8, RA-05).

The action database keeps its stored status words (``pending``, ``succeeded`` and so on; decision 2026-10-21, option A:
no rename of live data).  ``LEGACY_MAP`` and ``describe`` are the single place that turns a stored word into the
spec's lifecycle vocabulary, and a test pins them to the statuses the queue can store.

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

from catchup_core.actions import ActionError, ActionStatus, normalize_preconditions

__all__ = ["LEGACY_MAP", "MappedRow", "MigrationPlan", "audit_database", "describe", "plan_migration"]

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


def _core_preconditions_missing(row: Mapping[str, object]) -> bool:
    import json  # noqa: PLC0415
    is_core = row.get("origin") == "catchup_core" or row.get("source_skill") == "catchup_core"
    if not is_core:
        return False
    raw = row.get("preconditions_json") or row.get("preconditions")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        normalize_preconditions(parsed, core_derived=True)
        return False
    except (ValueError, TypeError, ActionError):
        return True


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
            if _core_preconditions_missing(row):
                target = ActionStatus.PROPOSED
                reason = "core-derived approval lacks bound Fact and permission preconditions; re-review"
        elif legacy in ("pending", "deferred", "modifying") and _core_preconditions_missing(row):
            review, reason = True, "core-derived proposal lacks bound Fact and permission preconditions"
        mapped.append(MappedRow(rid, legacy, target, review, reason))
    return MigrationPlan(tuple(mapped), tuple(unmapped))


def audit_database(path: Path | str) -> MigrationPlan:
    """Open the action database read-only and plan its migration."""
    uri = f"file:{Path(path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM actions")]
    finally:
        conn.close()
    return plan_migration(rows)


_GROUPS = {
    ActionStatus.DRAFT: "pending", ActionStatus.PROPOSED: "pending", ActionStatus.DEFERRED: "pending",
    ActionStatus.APPROVED: "approved", ActionStatus.EXECUTING: "executing", ActionStatus.UNKNOWN: "unknown",
    ActionStatus.SUCCEEDED: "completed", ActionStatus.FAILED: "completed", ActionStatus.REJECTED: "completed",
    ActionStatus.EXPIRED: "completed", ActionStatus.CANCELLED: "completed",
}


def describe(row: Mapping[str, object]) -> dict[str, object]:
    """One activity line in the spec's vocabulary (RA-24) without migrating the live database.

    ``lifecycle`` is the 7.8 status the legacy value maps to, ``group`` is the view bucket (pending, approved,
    executing, unknown, completed) and ``reversibility`` says whether an undo exists.  An unmapped legacy value is
    shown as ``unknown`` rather than guessed.
    """
    legacy = str(row.get("status", ""))
    target = LEGACY_MAP.get(legacy)
    if legacy == "executing":
        target = ActionStatus.EXECUTING              # in flight is not the same as "may have been lost" in a live view
    undo = row.get("undo_window_sec")
    reversible = bool(row.get("reversible"))
    return {
        "id": row.get("id"), "action_type": row.get("action_type"), "domain": row.get("domain"),
        "status": legacy, "lifecycle": (target or ActionStatus.UNKNOWN).value,
        "group": _GROUPS.get(target, "unknown") if target else "unknown",
        "reversibility": (f"reversible (undo window {undo}s)" if reversible and undo else
                          "reversible" if reversible else "irreversible"),
        "created_at": row.get("created_at"),
    }
