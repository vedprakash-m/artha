"""
scripts/lib/connector_coverage.py — per-run connector coverage (WP-02)
=======================================================================
A Connector run is *complete* only when its records were fetched, decoded and
validated; "returned something" is not enough (re-artha §6.7, §6.13, RA-16).

Status table (§6.13)
--------------------
=============================================  ==========================
condition                                       status
=============================================  ==========================
complete, valid, empty result                   ``complete`` (quiet allowed)
rejected record, failed/truncated scope         ``degraded``
unavailable source (auth, network, provider)    ``unavailable``
zero records where history usually has data     ``anomaly`` (disclose)
=============================================  ==========================

Only ``complete`` permits a scoped quiet claim.  The module is dependency-free
(stdlib only) so connectors may import it without pulling in the pipeline.
Handlers report per-scope outcomes with :func:`report_scope`; outside a tracked
run that call is a no-op, so handlers stay usable stand-alone.
"""
from __future__ import annotations

import contextvars
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

HISTORY_LIMIT = 10
ANOMALY_WINDOW = 5
ANOMALY_MIN_NONZERO = 3
CHRONIC_GAP_DAYS = 3          # re-artha §12.3 default; owner may override
MAX_REASONS = 5

_FAILING = {"unavailable", "degraded"}


@dataclass(frozen=True)
class ScopeResult:
    """Outcome for one scope inside a connector (a calendar id, a folder, ...)."""

    scope: str
    status: str                 # complete | failed | truncated
    records: int = 0
    reason: str = ""


@dataclass(frozen=True)
class ConnectorRun:
    """What one connector run actually delivered."""

    connector: str
    records: int
    rejected: int
    rejection_reasons: list[str]
    error: str | None
    scopes: list[ScopeResult]
    elapsed: float
    anomaly: bool = False

    def status(self) -> str:
        if self.error:
            return "unavailable"
        if self.rejected > 0 or any(s.status in ("failed", "truncated") for s in self.scopes):
            return "degraded"
        if self.anomaly:
            return "anomaly"
        return "complete"

    def quiet_claim_allowed(self) -> bool:
        return self.status() == "complete"

    def with_history(self, history: list[dict[str, Any]]) -> "ConnectorRun":
        return replace(self, anomaly=volume_anomaly(self, history))

    def describe(self) -> str:
        status = self.status()
        if status == "unavailable":
            return f"{self.connector} unavailable ({_short(self.error or '')})"
        if status == "anomaly":
            return f"{self.connector} returned nothing (it usually returns data)"
        parts: list[str] = []
        if self.rejected:
            parts.append(f"{self.rejected} record{'s' if self.rejected != 1 else ''} rejected")
        for scope in self.scopes:
            if scope.status in ("failed", "truncated"):
                parts.append(f"{scope.scope} {scope.status}")
        if status == "degraded":
            return f"{self.connector} degraded ({'; '.join(parts)})"
        return f"{self.connector} ok ({self.records})"

    def as_entry(self, at: str) -> dict[str, Any]:
        return {
            "at": at,
            "status": self.status(),
            "records": self.records,
            "rejected": self.rejected,
            "reasons": self.rejection_reasons[:MAX_REASONS],
            "error": _short(self.error or "") or None,
            "scopes": [asdict(s) for s in self.scopes],
        }


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def volume_anomaly(run: ConnectorRun, history: list[dict[str, Any]]) -> bool:
    """Zero records now, while at least 3 of the last 5 complete runs had data."""
    if run.records != 0 or run.error or run.rejected:
        return False
    recent = [h for h in history if h.get("status") in ("complete", "anomaly")][-ANOMALY_WINDOW:]
    return sum(1 for h in recent if h.get("records", 0) > 0) >= ANOMALY_MIN_NONZERO


# ---------------------------------------------------------------------------
# Handler-side reporting
# ---------------------------------------------------------------------------

_current: contextvars.ContextVar["_Tracker | None"] = contextvars.ContextVar("artha_cov", default=None)


class _Tracker:
    def __init__(self, connector: str):
        self.connector = connector
        self.scopes: list[ScopeResult] = []


def report_scope(scope: str, status: str, *, records: int = 0, reason: str = "") -> None:
    """Called by a handler for each scope it attempted.  No-op outside a tracked run."""
    tracker = _current.get()
    if tracker is not None:
        tracker.scopes.append(ScopeResult(scope, status, records, _short(reason)))


class CoverageCollector:
    """Creates per-connector trackers; safe to share across worker threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.runs: list[ConnectorRun] = []

    @contextmanager
    def track(self, connector: str) -> Iterator[_Tracker]:
        tracker = _Tracker(connector)
        token = _current.set(tracker)
        try:
            yield tracker
        finally:
            _current.reset(token)

    def add(self, run: ConnectorRun) -> None:
        with self._lock:
            self.runs.append(run)


# ---------------------------------------------------------------------------
# Persistence (counts and statuses only — no record content)
# ---------------------------------------------------------------------------

def default_path() -> Path:
    local = os.environ.get("ARTHA_LOCAL_DIR") or str(Path.home() / ".artha-local")
    return Path(local).expanduser() / "connector_coverage.json"


class CoverageStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_path()

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("connectors"), dict):
                return data
        except (OSError, ValueError):
            pass
        return {"connectors": {}}

    def history(self, connector: str) -> list[dict[str, Any]]:
        return list(self.load()["connectors"].get(connector, {}).get("history", []))

    def record(self, run: ConnectorRun, at: str | None = None) -> dict[str, Any]:
        at = at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = self.load()
        entry = data["connectors"].setdefault(run.connector, {})
        history = entry.get("history", [])
        scored = run.with_history(history)
        row = scored.as_entry(at)
        history = (history + [row])[-HISTORY_LIMIT:]
        entry["history"] = history
        entry["last_run"] = row
        if row["status"] == "complete":
            entry["last_success_at"] = at
            entry.pop("failing_since", None)
        elif row["status"] in _FAILING:
            entry.setdefault("failing_since", at)
        data["generated_at"] = at
        self._write(data)
        return row

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".cov-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _days_since(iso: str, now: str | None) -> int:
    def parse(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    current = parse(now) if now else datetime.now(timezone.utc)
    return max(0, (current - parse(iso)).days)


def coverage_line(
    runs: list[ConnectorRun],
    entries: dict[str, Any] | None = None,
    now: str | None = None,
) -> str:
    """One compact Briefing line.  Never says "all clear" for a degraded scope."""
    entries = entries or {}
    gaps: list[str] = []
    for run in runs:
        scored = run
        if run.status() != "complete":
            gaps.append(run.describe())
        since = (entries.get(run.connector) or {}).get("failing_since")
        if since and scored.status() in _FAILING:
            days = _days_since(since, now)
            if days >= CHRONIC_GAP_DAYS:
                gaps.append(f"{run.connector} failing {days} days")
    if not gaps:
        return "Coverage: all required sources checked."
    unique = list(dict.fromkeys(gaps))
    return "Coverage: " + "; ".join(unique) + " — no all-clear for these scopes."


def runs_from_store(data: dict[str, Any]) -> list[ConnectorRun]:
    """Rebuild each connector's *last* run from the persisted store."""
    runs: list[ConnectorRun] = []
    for name, entry in sorted((data.get("connectors") or {}).items()):
        last = (entry or {}).get("last_run") or {}
        scopes = [
            ScopeResult(s.get("scope", "?"), s.get("status", "complete"),
                        int(s.get("records", 0) or 0), s.get("reason", ""))
            for s in last.get("scopes", [])
        ]
        run = ConnectorRun(
            connector=name, records=int(last.get("records", 0) or 0),
            rejected=int(last.get("rejected", 0) or 0),
            rejection_reasons=list(last.get("reasons", [])),
            error=last.get("error"), scopes=scopes, elapsed=0.0,
            anomaly=last.get("status") == "anomaly",
        )
        runs.append(run)
    return runs


def line_from_store(store: "CoverageStore | None" = None, now: str | None = None) -> str:
    """Briefing coverage line from the persisted last fetch, with its age.

    Never claims coverage when no fetch is on record, and always states how old
    the fetch is — a Digest must not imply that sources were just checked.
    """
    store = store or CoverageStore()
    data = store.load()
    if not data.get("connectors"):
        return "Coverage: no fetch recorded — sources were not checked in this Catch-up."
    runs = runs_from_store(data)
    body = coverage_line(runs, data.get("connectors"), now=now)
    stamp = data.get("generated_at")
    if stamp:
        try:
            current = datetime.fromisoformat((now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                                             .replace("Z", "+00:00"))
            hours = max(0.0, (current - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds() / 3600)
            age = "under 1h ago" if hours < 1 else (f"{int(hours)}h ago" if hours < 48 else f"{int(hours // 24)} days ago")
            return body.replace("Coverage:", f"Coverage (fetched {age}):", 1)
        except ValueError:
            pass
    return body


__all__ = [
    "ANOMALY_MIN_NONZERO", "ANOMALY_WINDOW", "CHRONIC_GAP_DAYS", "HISTORY_LIMIT",
    "ConnectorRun", "CoverageCollector", "CoverageStore", "ScopeResult",
    "coverage_line", "default_path", "line_from_store", "report_scope", "runs_from_store",
    "volume_anomaly",
]
