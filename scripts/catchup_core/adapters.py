"""Adapter from the incumbent Connector handlers to the core's ``ConnectorFetch`` (§6.5, §7.10).

The handlers' ``fetch()`` yields records and nothing else.  This adapter recovers the
completion information the core needs without rewriting any provider code:

* each record goes through the real validator; a rejection degrades the scope;
* per-scope outcomes the handlers report (``lib.connector_coverage.report_scope``)
  become coverage — a failed or truncated scope is *degraded*, never "empty";
* an exception is *unavailable*, classified into the bounded failure taxonomy;
* the text handed to extraction is built from the record's own fields, nothing else.
"""
from __future__ import annotations

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
_NET = re.compile(r"(?i)timed? ?out|connection|dns|network|unreachable|\b50[234]\b|name or service")
MAX_TEXT = 12_000


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


def fetch_via_handler(
    name: str,
    account_scope: str,
    fetch: Callable[..., Iterable[dict[str, Any]]],
    *,
    since: str,
    auth_context: Mapping[str, Any] | None = None,
    fetch_kwargs: Mapping[str, Any] | None = None,
    text_of: Callable[[Mapping[str, Any]], str] = default_text,
    required: bool = True,
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
                if raw.get("status") == "cancelled" and isinstance(raw.get("id"), str):
                    cancelled.append(raw["id"])
                    continue
                try:
                    validated = validate_record(raw)
                    calendar = _calendar(raw)
                except (TypeError, ValueError):
                    rejected += 1
                    continue
                incomplete = bool(raw.get("attachments") or raw.get("body_truncated")
                                  or raw.get("processing_gaps"))
                if incomplete:
                    content_gaps.append("attachment or omitted content has not been interpreted")
                records.append(ConnectorRecord(
                    upstream_id=validated.id, text=text_of(raw), provider_version=_provider_version(raw),
                    occurred_at=validated.date_iso, incomplete=incomplete, calendar=calendar))
            scopes = list(tracker.scopes)
    except Exception as exc:
        return ConnectorFetch(name, account_scope, (), CoverageStatus.UNAVAILABLE, classify_failure(exc),
                              blind_spots=(f"fetch failed: {type(exc).__name__}",), required=required)

    blind = [f"{s.scope}: {s.status}" + (f" ({s.reason})" if s.reason else "")
             for s in scopes if s.status in ("failed", "truncated")]
    if rejected:
        blind.append(f"{rejected} record(s) rejected by the validator")
    blind.extend(sorted(set(content_gaps)))

    try:
        from lib.connector_coverage import ConnectorRun, CoverageStore  # noqa: PLC0415
        store = CoverageStore()
        c_run = ConnectorRun(
            connector=name,
            records=len(records),
            rejected=rejected,
            rejection_reasons=[f"{rejected} record(s) rejected"] if rejected else [],
            error=None,
            scopes=scopes,
            elapsed=0.0,
        )
        collector.add(c_run)
        stored = store.record(c_run)
        if stored.get("status") == "anomaly":
            return ConnectorFetch(name, account_scope, tuple(records), CoverageStatus.ANOMALY,
                                  blind_spots=("unexpected volume drop against complete history",), required=required)
    except Exception:  # noqa: S110
        pass

    if blind:
        return ConnectorFetch(name, account_scope, tuple(records), CoverageStatus.DEGRADED,
                              FailureClass.SCHEMA_REJECTED if rejected else FailureClass.PROVIDER,
                              blind_spots=tuple(blind), required=required)
    return replace(ConnectorFetch(name, account_scope, tuple(records), CoverageStatus.COMPLETE, None,
                                  required=required), cancelled=tuple(cancelled))


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
    return CalendarEvent(str(raw["id"]), start, end, all_day, kind)

