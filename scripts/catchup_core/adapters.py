"""Adapter from the incumbent Connector handlers to the core's ``ConnectorFetch`` (§6.5, §7.10).

The handlers' ``fetch()`` yields records and nothing else.  This adapter recovers the
completion information the core needs without rewriting any provider code:

* each record goes through the real validator; a rejection degrades the scope;
* per-scope outcomes the handlers report (``lib.connector_coverage.report_scope``)
  become coverage — a failed or truncated scope is *degraded*, never "empty";
* an exception retains validated positives/cancellations under degraded coverage; without useful data it is unavailable;
* the text handed to extraction is built from the record's own fields, nothing else.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import date, datetime
from typing import Any

from catchup_core.contracts import CoverageStatus, FailureClass
from catchup_core.coordinator import ConnectorFetch, ConnectorRecord
from catchup_core.triage import CalendarEvent
from lib.connector_coverage import CoverageCollector
from schemas.connector_record import validate_record

__all__ = ["classify_failure", "default_text", "fetch_via_handler"]

_AUTH = re.compile(r"(?i)invalid_grant|unauthori[sz]ed|forbidden|\b40[13]\b|token (expired|revoked)|auth")
_UNAUTHENTICATED = re.compile(r"(?i)\b401\b|invalid_grant|unauthori[sz]ed|token (expired|revoked)")
_NET = re.compile(r"(?i)timed? ?out|connection|dns|network|unreachable|\b50[234]\b|name or service")
_RESET = re.compile(r"(?i)\b410\b|sync token invalid|fullSyncRequired|resyncRequired|expired delta|delta token invalid")
MAX_TEXT = 12_000


def calendar_identity(record: Mapping[str, Any]) -> str:
    """Provider event IDs are only unique within their calendar. Keep unscoped legacy IDs readable."""
    if record.get("calendar_id"):
        return "calendar:" + json.dumps([str(record["calendar_id"]), str(record["id"])], separators=(",", ":"))
    return str(record["id"])


def classify_failure(exc: BaseException) -> FailureClass:
    text = f"{type(exc).__name__} {exc}"
    if _AUTH.search(text):
        return FailureClass.AUTH
    if isinstance(exc, TimeoutError) or _NET.search(text):
        return FailureClass.NETWORK
    return FailureClass.PROVIDER


def default_text(record: Mapping[str, Any]) -> str:
    """Plain text for extraction, from email or calendar record fields."""
    if "subject" in record or "body" in record:
        parts = [str(record.get("subject", "")), str(record.get("body", ""))]
    else:
        parts = [str(record.get("summary", "")), f"Start: {record.get('start', '')}",
                 f"End: {record.get('end', '')}", str(record.get("location", "")),
                 str(record.get("description", ""))]
    return "\n".join(p for p in parts if p.strip())


def _provider_version(record: Mapping[str, Any]) -> str | None:
    for key in ("version", "etag", "changeKey", "updated", "sequence"):
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def fetch_via_handler(  # noqa: PLR0912, PLR0915
    name: str,
    account_scope: str,
    fetch: Callable[..., Iterable[dict[str, Any]]],
    *,
    since: str,
    auth_context: Mapping[str, Any] | None = None,
    fetch_kwargs: Mapping[str, Any] | None = None,
    text_of: Callable[[Mapping[str, Any]], str] = default_text,
    required: bool = True,
    calendar_kinds: Mapping[str, str] | None = None,
) -> ConnectorFetch:
    collector = CoverageCollector()
    records: list[ConnectorRecord] = []
    cancelled: list[str] = []
    rejected = 0
    content_gaps: list[str] = []
    try:
        with collector.track(name) as tracker:
            for raw in fetch(since=since, auth_context=dict(auth_context or {}), source_tag=name,
                             **dict(fetch_kwargs or {})):
                if not isinstance(raw, dict):
                    rejected += 1
                    continue
                if raw.get("status") == "cancelled":
                    if not isinstance(raw.get('id'), str) or not raw['id'].strip():
                        rejected += 1
                        continue
                    cancelled.append(calendar_identity(raw))
                    continue
                try:
                    validated = validate_record(raw)
                    calendar_raw = dict(raw)
                    if calendar_kinds is not None:
                        calendar_raw["kind"] = calendar_kinds.get(str(raw.get("calendar_id", "")), "other")
                    calendar = _calendar(calendar_raw)
                except (TypeError, ValueError):
                    rejected += 1
                    continue
                incomplete = bool(raw.get("attachments") or raw.get("body_truncated")
                                  or raw.get("processing_gaps"))
                if incomplete:
                    content_gaps.append("attachment or omitted content has not been interpreted")
                records.append(ConnectorRecord(
                    upstream_id=calendar_identity(raw) if calendar else validated.id,
                    text=text_of(raw), provider_version=_provider_version(raw),
                    occurred_at=validated.date_iso, incomplete=incomplete, calendar=calendar))
            scopes = list(tracker.scopes)
            cursor = tracker.cursor
            cursor_reset = tracker.cursor_reset
            deleted_collections = tuple(tracker.deleted_collections)
            delta = tracker.delta
            full_listings = tuple((c, w[0], w[1]) for c, w in sorted(tracker.full_listings.items()))
    except Exception as exc:
        is_reset = tracker.cursor_reset or bool(_RESET.search(f"{type(exc).__name__} {exc}"))
        deleted_collections = tuple(tracker.deleted_collections)
        status = CoverageStatus.DEGRADED if records or cancelled or deleted_collections else CoverageStatus.UNAVAILABLE
        blind = [f"fetch failed: {type(exc).__name__}"]
        if is_reset:
            blind.append("provider cursor reset: full resync required")
        return ConnectorFetch(name, account_scope, tuple(records), status, classify_failure(exc),
                              blind_spots=tuple(blind), required=required,
                              cancelled=tuple(cancelled), cursor_reset=is_reset,
                              deleted_collections=deleted_collections)

    blind = [f"{s.scope}: {s.status}" + (f" ({s.reason})" if s.reason else "")
             for s in scopes if s.status in ("failed", "truncated")]
    if rejected:
        blind.append(f"{rejected} record(s) rejected by the validator")
    blind.extend(sorted(set(content_gaps)))

    try:
        from lib.connector_coverage import ConnectorRun, CoverageStore, default_path  # noqa: PLC0415
        # Old incumbent counts have no account/filter attribution and cannot establish this scope's baseline.
        # A cursor changes every run; it is not part of the source's identity (or its history would never build up).
        stable = {k: v for k, v in dict(fetch_kwargs or {}).items() if k not in ('sync_tokens', 'sync_cursor')}
        scope = json.dumps([name, account_scope, stable], sort_keys=True, separators=(',', ':'))
        key = hashlib.sha256(scope.encode()).hexdigest()
        store = CoverageStore(default_path().parent / 'catchup_coverage' / f'{key}.json')
        c_run = ConnectorRun(
            connector=name,
            records=len(records),
            rejected=rejected,
            rejection_reasons=[f"{rejected} record(s) rejected"] if rejected else [],
            error=None,
            scopes=scopes,
            elapsed=0.0,
            delta=delta,
        )
        collector.add(c_run)
        stored = store.record(c_run)
        if stored.get("status") == "anomaly":
            return ConnectorFetch(name, account_scope, tuple(records), CoverageStatus.ANOMALY,
                                  blind_spots=("unexpected volume drop against complete history",), required=required,
                                  cancelled=tuple(cancelled), cursor_reset=cursor_reset,
                                  deleted_collections=deleted_collections)
    except Exception:  # noqa: S110
        pass

    if blind:
        # A scope that failed with 401 is a loss of access; 403 stays generic (providers also use it for quota).
        denied = any(s.status == "failed" and _UNAUTHENTICATED.search(s.reason or "") for s in scopes)
        return ConnectorFetch(name, account_scope, tuple(records), CoverageStatus.DEGRADED,
                              FailureClass.SCHEMA_REJECTED if rejected else
                              FailureClass.AUTH if denied else FailureClass.PROVIDER,
                              blind_spots=tuple(blind), required=required, cancelled=tuple(cancelled),
                              cursor_reset=cursor_reset,
                              deleted_collections=deleted_collections)
    return replace(ConnectorFetch(name, account_scope, tuple(records), CoverageStatus.COMPLETE, None,
                                  required=required, cursor=cursor, cursor_reset=cursor_reset,
                                  deleted_collections=deleted_collections, full_listings=full_listings),
                   cancelled=tuple(cancelled))


def _calendar(raw: Mapping[str, Any]) -> CalendarEvent | None:
    if "start" not in raw:
        return None
    all_day = bool(raw.get("all_day", "T" not in str(raw["start"])))
    parse = date.fromisoformat if all_day else datetime.fromisoformat
    start, end = parse(str(raw["start"])), parse(str(raw.get("end", raw["start"])))
    if end < start or (not all_day and (not isinstance(start, datetime) or not isinstance(end, datetime)
                                      or start.tzinfo is None or end.tzinfo is None)):
        raise ValueError("calendar bounds require ordered dates or offset-aware times")
    kind = str(raw.get("kind", "other"))
    if kind not in ("school", "travel", "out_of_office", "other"):
        raise ValueError("unknown calendar kind")
    return CalendarEvent(calendar_identity(raw), start, end, all_day, kind)

