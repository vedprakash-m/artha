"""telemetry.py — Structured logging, metrics, and event spans for pr_stage.

All stage operations emit structured log lines to:
  - state/audit.md (append-only audit trail)
  - tmp/stage_events.jsonl (event journal, spec §4.6)

Metrics are in-memory counters reset each catch-up cycle. They are
surfaced to the briefing via ContentStage.get_metrics().

Spec: §4.6, §11
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Event journal (§4.6)
# ─────────────────────────────────────────────────────────────────────────────

JOURNAL_MAX_AGE_DAYS = 30  # Events older than this are pruned during vault encrypt


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StageLogger:
    """Structured logger for ContentStage operations.

    Writes to:
      1. tmp/stage_events.jsonl — append-only event journal
      2. state/audit.md — high-level audit trail
    """

    def __init__(self, journal_path: Path, audit_path: Path) -> None:
        self._journal = journal_path
        self._audit   = audit_path
        self._metrics: dict[str, int] = {
            "cards_created":          0,
            "cards_auto_drafted":     0,
            "cards_staged":           0,
            "cards_archived":         0,
            "pii_failures":           0,
            "stage_pii_failures_total": 0,
            "auto_draft_failures":    0,
        }
        self._start = time.monotonic()

    # ── public metric increment ─────────────────────────────────────────

    def inc(self, counter: str, n: int = 1) -> None:
        self._metrics[counter] = self._metrics.get(counter, 0) + n

    def get_metrics(self) -> dict[str, int]:
        return dict(self._metrics)

    # ── event logging ──────────────────────────────────────────────────

    def event(
        self,
        event_type: str,
        *,
        card_id: str | None = None,
        platform: str | None = None,
        from_state: str | None = None,
        to_state: str | None = None,
        result: str = "ok",
        elapsed_ms: int | None = None,
        **extra: Any,
    ) -> None:
        """Write a single event to the journal and audit log."""
        entry: dict[str, Any] = {
            "ts":       _now_iso(),
            "event":    event_type,
            "result":   result,
        }
        if card_id is not None:
            entry["card_id"]    = card_id
        if platform is not None:
            entry["platform"]   = platform
        if from_state is not None:
            entry["from_state"] = from_state
        if to_state is not None:
            entry["to_state"]   = to_state
        if elapsed_ms is not None:
            entry["elapsed_ms"] = elapsed_ms
        entry.update(extra)

        self._append_journal(entry)
        self._append_audit(event_type, card_id, result)

    def _append_journal(self, entry: dict) -> None:
        try:
            self._journal.parent.mkdir(parents=True, exist_ok=True)
            with open(self._journal, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # Journal write failure is non-fatal

    def _append_audit(self, event_type: str, card_id: str | None, result: str) -> None:
        try:
            now = _now_iso()
            card_part = f" | card: {card_id}" if card_id else ""
            line = f"[{now}] STAGE | {event_type}{card_part} | result: {result}\n"
            with open(self._audit, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass  # Audit write failure is non-fatal

    # ── journal pruning (called by vault encrypt handler) ───────────────

    def prune_journal(self) -> int:
        """Remove journal entries older than JOURNAL_MAX_AGE_DAYS.

        Returns the number of entries pruned.
        """
        if not self._journal.exists():
            return 0

        cutoff_ts = time.time() - (JOURNAL_MAX_AGE_DAYS * 86400)
        kept: list[str] = []
        pruned = 0

        try:
            with open(self._journal, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts_str = entry.get("ts", "")
                        entry_ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                            tzinfo=timezone.utc
                        ).timestamp()
                        if entry_ts >= cutoff_ts:
                            kept.append(line)
                        else:
                            pruned += 1
                    except (json.JSONDecodeError, ValueError):
                        kept.append(line)  # Keep unparseable lines

            if pruned > 0:
                with open(self._journal, "w", encoding="utf-8") as f:
                    f.write("\n".join(kept))
                    if kept:
                        f.write("\n")
        except OSError:
            pass

        return pruned
