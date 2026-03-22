#!/usr/bin/env python3
# pii-guard: reminder content is standard sensitivity; no financial/medical data
"""
scripts/connectors/apple_reminders.py — Apple Reminders connector via macOS EventKit.

Read-only ingestion of Apple Reminders for open item tracking and task sync.
macOS only — gracefully skips on Windows/Linux with a logged warning.
Requires pyobjc-framework-EventKit: pip install 'artha[apple]'

ConnectorHandler protocol: module-level fetch() + health_check() functions.

Ref: specs/connect.md §6.2
"""
from __future__ import annotations

import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_IS_MACOS = platform.system() == "Darwin"

# ---------------------------------------------------------------------------
# Platform guard — non-macOS stubs
# ---------------------------------------------------------------------------

if not _IS_MACOS:
    import logging as _logging
    _logging.getLogger(__name__).info(
        "apple_reminders: macOS only — gracefully disabled on %s", platform.system()
    )

    def fetch(**_: Any) -> Iterator[dict]:  # type: ignore[misc]
        """No-op on non-macOS platforms."""
        return iter([])

    def health_check(_: Any = None) -> bool:  # type: ignore[misc]
        """Always False on non-macOS platforms."""
        return False

else:
    # ---------------------------------------------------------------------------
    # macOS EventKit implementation
    # ---------------------------------------------------------------------------

    def _get_event_store():
        """Create and return an EKEventStore instance (macOS only)."""
        from EventKit import EKEventStore, EKEntityTypeReminder  # type: ignore[import]
        import Foundation  # type: ignore[import]

        store = EKEventStore.alloc().init()

        # Request access (TCC permission dialog on first run)
        _granted = [False]
        _done = [False]

        def _handler(granted, error):  # noqa: ANN001
            _granted[0] = granted
            _done[0] = True

        store.requestAccessToEntityType_completion_(EKEntityTypeReminder, _handler)

        # Wait for TCC response (blocking for up to 10 seconds)
        import time  # noqa: PLC0415
        deadline = time.time() + 10.0
        while not _done[0] and time.time() < deadline:
            Foundation.NSRunLoop.currentRunLoop().runUntilDate_(
                Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.1)
            )

        if not _granted[0]:
            raise PermissionError(
                "Apple Reminders access denied. "
                "Grant permission in System Settings → Privacy & Security → Reminders."
            )

        return store

    def _fetch_reminders_from_store(
        store: Any,
        calendar_filter: list[str] | None,
        since_dt: datetime | None,
        max_results: int,
        sync_completed: bool,
    ) -> list[dict]:
        """Fetch active (and optionally completed) reminders from EventKit store."""
        from EventKit import EKEntityTypeReminder  # type: ignore[import]
        import Foundation  # type: ignore[import]

        # Get reminder calendars
        calendars = store.calendarsForEntityType_(EKEntityTypeReminder)
        if calendar_filter:
            cf_lower = {c.lower() for c in calendar_filter}
            calendars = [c for c in calendars if c.title().lower() in cf_lower]

        records: list[dict] = []
        predicates_done = [False]

        def _complete_handler(reminders):  # noqa: ANN001
            for r in (reminders or []):
                if len(records) >= max_results:
                    break
                due_date = r.dueDateComponents()
                if due_date:
                    try:
                        cal = Foundation.NSCalendar.currentCalendar()
                        ns_date = cal.dateFromComponents_(due_date)
                        due_str = datetime.fromtimestamp(
                            ns_date.timeIntervalSince1970(), tz=timezone.utc
                        ).isoformat()
                    except Exception:
                        due_str = ""
                else:
                    due_str = ""

                completed_date = ""
                if r.isCompleted() and r.completionDate():
                    try:
                        completed_date = datetime.fromtimestamp(
                            r.completionDate().timeIntervalSince1970(), tz=timezone.utc
                        ).isoformat()
                    except Exception:
                        completed_date = ""

                records.append({
                    "id": str(r.calendarItemIdentifier()),
                    "title": str(r.title() or ""),
                    "notes": str(r.notes() or ""),
                    "due_date": due_str,
                    "is_completed": bool(r.isCompleted()),
                    "completed_date": completed_date,
                    "priority": int(r.priority()),
                    "calendar_name": str(r.calendar().title()),
                })
            predicates_done[0] = True

        # Build predicate
        predicate = store.predicateForRemindersInCalendars_(calendars)
        store.fetchRemindersMatchingPredicate_completion_(predicate, _complete_handler)

        import time  # noqa: PLC0415
        deadline = time.time() + 15.0
        while not predicates_done[0] and time.time() < deadline:
            Foundation.NSRunLoop.currentRunLoop().runUntilDate_(
                Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.05)
            )

        return records

    def _since_to_datetime(since: str | None) -> datetime | None:
        """Parse since string to datetime."""
        if not since:
            return None
        import re  # noqa: PLC0415
        m = re.match(r"^(\d+)([dhm])$", since.strip(), re.I)
        if m:
            from datetime import timedelta  # noqa: PLC0415
            n, unit = int(m.group(1)), m.group(2).lower()
            delta = {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]
            return datetime.now(timezone.utc) - delta
        try:
            return datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            return None

    def fetch(  # type: ignore[misc]
        *,
        since: str | None = None,
        max_results: int = 500,
        auth_context: dict | None = None,
        source_tag: str = "apple_reminders",
        calendar_filter: list[str] | None = None,
        sync_completed: bool = True,
        **kwargs: Any,
    ) -> Iterator[dict]:
        """Fetch Apple Reminders and yield Artha connector records (macOS only).

        Args:
            since: Lookback window for completed reminders ("7d", "24h", ISO).
            max_results: Maximum items to yield.
            auth_context: Unused for Apple Reminders (TCC permission, no token).
            source_tag: Source identifier injected into each record.
            calendar_filter: Optional list of Reminders list names.
                None = all lists.
            sync_completed: If True, also yield recently completed reminders.
        """
        import logging  # noqa: PLC0415
        log = logging.getLogger(__name__)

        try:
            store = _get_event_store()
        except ImportError:
            log.warning(
                "apple_reminders: pyobjc-framework-EventKit not installed. "
                "Run: pip install 'artha[apple]'"
            )
            return
        except PermissionError as exc:
            log.warning("apple_reminders: %s", exc)
            return
        except Exception as exc:
            log.error("apple_reminders: store initialization failed: %s", exc)
            return

        since_dt = _since_to_datetime(since)

        try:
            records = _fetch_reminders_from_store(
                store, calendar_filter, since_dt, max_results, sync_completed
            )
        except Exception as exc:
            log.error("apple_reminders: fetch failed: %s", exc)
            return

        for rec in records:
            if not sync_completed and rec["is_completed"]:
                continue
            yield {
                **rec,
                "source": source_tag,
                # Artha pipeline compatibility
                "title": rec["title"],
                "body": rec["notes"],
                "date_iso": rec.get("due_date", "") or datetime.now(timezone.utc).isoformat(),
            }

    def health_check(auth_context: dict | None = None) -> bool:  # type: ignore[misc]
        """Verify EventKit access is granted. Returns True if TCC allows Reminders."""
        try:
            _get_event_store()
            return True
        except ImportError:
            return False
        except PermissionError:
            return False
        except Exception:
            return False
