"""Canonical synthetic fixtures and advancing test clocks shared across test tiers.

Centralizes synthetic Signal, Email, Calendar, Goal, and deterministic clock
builders so consolidated and new tests never invent divergent payload shapes
or use wall-clock ``time.sleep()`` calls just to separate ISO timestamps.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


class AdvancingClock:
    """Deterministic UTC clock that advances by ``step`` on each ``now()`` call."""

    def __init__(
        self,
        start: datetime | None = None,
        *,
        step: timedelta = timedelta(seconds=2),
    ) -> None:
        self._current = start or datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)
        self._step = step

    def now(self, tz: Any = None) -> datetime:
        value = self._current
        self._current = self._current + self._step
        if tz is not None:
            return value.astimezone(tz)
        return value


class PatchedDateTime:
    """Drop-in ``datetime`` shim whose ``now()`` advances monotonically."""

    strptime = staticmethod(datetime.strptime)
    fromisoformat = staticmethod(datetime.fromisoformat)

    def __init__(self, clock: AdvancingClock | None = None) -> None:
        self._clock = clock or AdvancingClock()

    def now(self, tz: Any = None) -> datetime:
        return self._clock.now(tz)

    def __call__(self, *args: Any, **kwargs: Any) -> datetime:
        return datetime(*args, **kwargs)


def make_email_record(
    rec_id: str = "email-synth-001",
    *,
    source: str = "google_email",
    subject: str = "Synthetic update",
    body: str = "Synthetic body content",
    date_iso: str = "2026-11-16T09:00:00Z",
    sender: str = "sender@example.invalid",
    **extra: Any,
) -> dict[str, Any]:
    """Build a schema-valid synthetic email connector record."""
    return {
        "id": rec_id,
        "source": source,
        "subject": subject,
        "body": body,
        "from": sender,
        "date_iso": date_iso,
        **extra,
    }


def make_calendar_record(
    rec_id: str = "cal-synth-001",
    *,
    source: str = "google_calendar",
    summary: str = "Synthetic deadline review",
    start: str = "2026-11-18T10:00:00Z",
    end: str = "2026-11-18T11:00:00Z",
    calendar_id: str = "primary",
    **extra: Any,
) -> dict[str, Any]:
    """Build a schema-valid synthetic calendar connector record."""
    return {
        "id": rec_id,
        "source": source,
        "summary": summary,
        "start": start,
        "end": end,
        "calendar_id": calendar_id,
        **extra,
    }


def make_goal_record(
    goal_id: str = "goal-synth-001",
    *,
    title: str = "Synthetic weekly milestone",
    status: str = "active",
    target: float = 100.0,
    current: float = 50.0,
    unit: str = "percent",
    **extra: Any,
) -> dict[str, Any]:
    """Build a synthetic Goal record with source-backed numeric state."""
    return {
        "id": goal_id,
        "title": title,
        "status": status,
        "target": target,
        "current": current,
        "unit": unit,
        **extra,
    }
