#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/action_rate_limiter.py — Per-action-type rate limiting.

Enforces hourly and daily caps defined in config/actions.yaml per action type.
This is separate from the per-provider API rate limiter in
scripts/middleware/rate_limiter.py — that one guards raw API calls;
this one guards the action execution pipeline.

Design:
  Uses sliding-window counters backed by the SQLite action_audit table to
  count executions.  In-memory counters with DB fallback on first call.
  Thread-safe via per-type locks.

Ref: specs/act.md §6.1, §13.1
"""
from __future__ import annotations

import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


class RateLimitError(Exception):
    """Raised when an action type's rate limit is exceeded."""

    def __init__(self, action_type: str, window: str, count: int, limit: int) -> None:
        super().__init__(
            f"Rate limit exceeded for '{action_type}': "
            f"{count}/{limit} executions in the last {window}"
        )
        self.action_type = action_type
        self.window = window
        self.count = count
        self.limit = limit


class ActionRateLimiter:
    """Per-action-type sliding-window rate limiter.

    Limits are defined per action type in config/actions.yaml:
        rate_limit:
          max_per_hour: 20
          max_per_day:  100

    Usage:
        limiter = ActionRateLimiter(artha_dir, action_configs)
        limiter.check("email_send")   # raises RateLimitError if exceeded

    Thread safety: per-type locks.
    """

    def __init__(
        self,
        artha_dir: Path,
        action_configs: dict[str, dict[str, Any]],
    ) -> None:
        self._artha_dir = artha_dir
        self._db_path = artha_dir / "state" / "actions.db"
        self._configs = action_configs
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

    def check(self, action_type: str) -> None:
        """Check rate limits for action_type.

        Raises RateLimitError if hourly or daily cap is exceeded.
        No-op if no rate_limit config exists for this action type.
        """
        config = self._configs.get(action_type, {})
        rate_cfg = config.get("rate_limit", {})
        if not rate_cfg:
            return

        max_per_hour = rate_cfg.get("max_per_hour")
        max_per_day = rate_cfg.get("max_per_day")

        if not self._db_path.exists():
            return  # no DB yet — first run

        with self._locks[action_type]:
            conn = sqlite3.connect(str(self._db_path), timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=3000")
            try:
                if max_per_hour is not None:
                    count_hour = self._count_executions(conn, action_type, hours=1)
                    if count_hour >= max_per_hour:
                        raise RateLimitError(
                            action_type, "1 hour", count_hour, max_per_hour
                        )
                if max_per_day is not None:
                    count_day = self._count_executions(conn, action_type, hours=24)
                    if count_day >= max_per_day:
                        raise RateLimitError(
                            action_type, "24 hours", count_day, max_per_day
                        )
            finally:
                conn.close()

    def _count_executions(
        self, conn: sqlite3.Connection, action_type: str, hours: int
    ) -> int:
        """Count successful/partial executions of action_type in the last N hours."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat(timespec="seconds")

        row = conn.execute(
            """SELECT COUNT(*) FROM action_audit aa
               JOIN actions a ON a.id = aa.action_id
               WHERE a.action_type = ?
               AND aa.to_status IN ('executing', 'succeeded')
               AND aa.timestamp >= ?""",
            (action_type, cutoff),
        ).fetchone()
        return row[0] if row else 0
