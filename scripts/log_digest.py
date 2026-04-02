#!/usr/bin/env python3
"""scripts/log_digest.py — Aggregate structured JSONL logs into a digest.

Reads all artha.*.log.jsonl files from ~/.artha-local/logs/ and produces
a summary JSON written to tmp/log_digest.json.

Per-connector metrics:
    error_rate      — errors / total records (0.0–1.0)
    p95_ms          — 95th percentile latency (ms); None if no ms field
    record_count    — total records for this connector in the window

Global metrics:
    error_budget_pct    — (total_errors / total_records) * 100
    anomalies           — list of anomaly dicts (code, severity, connector, msg)

Anomaly detection (threshold: 3 types):
    HIGH_ERROR_RATE     — connector error_rate > 0.20
    TREND_WORSENING     — 7-day error_rate > 14-day error_rate by >5pp (needs ≥14d data)
    QUIET_FAILURE       — connector has 0 records in 3+ sessions

Ref: specs/eval.md EV-6
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent

try:
    from _bootstrap import reexec_in_venv  # type: ignore[import]
    reexec_in_venv()
except ImportError:
    pass

_LOG_DIR = Path(os.path.expanduser("~")) / ".artha-local" / "logs"
_TMP_DIR = _REPO_ROOT / "tmp"
_DIGEST_FILE = _TMP_DIR / "log_digest.json"

# Public alias — allows test monkeypatching: `monkeypatch.setattr(digest_mod, "TMP_DIR", ...)`
TMP_DIR = _TMP_DIR
_SCHEMA_VERSION = "1.0.0"

_ERROR_LEVELS = {"ERROR", "CRITICAL"}
_HIGH_ERROR_THRESHOLD = 0.20
_TREND_MIN_DAYS = 14          # Need 14+ days of data for trend detection
_TREND_WORSENING_PP = 0.05    # 5 percentage point worsening = anomaly


# ---------------------------------------------------------------------------
# Log file discovery and parsing
# ---------------------------------------------------------------------------

def _discover_log_files(log_dir: Path, lookback_days: int = 14) -> list[Path]:
    """Return all JSONL log files within the lookback window (sorted by date)."""
    if not log_dir.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    found: list[Path] = []
    for f in sorted(log_dir.glob("artha.*.log.jsonl")):
        # artha.YYYY-MM-DD.log.jsonl
        m = re.search(r"artha\.(\d{4}-\d{2}-\d{2})\.log\.jsonl$", f.name)
        if m:
            try:
                file_date = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if file_date >= cutoff:
                    found.append(f)
            except ValueError:
                pass
    return found


def _file_date(path: Path) -> datetime | None:
    """Extract date from artha.YYYY-MM-DD.log.jsonl filename."""
    m = re.search(r"artha\.(\d{4}-\d{2}-\d{2})\.log\.jsonl$", path.name)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _parse_records(path: Path) -> list[dict[str, Any]]:
    """Parse JSONL file, silently skip malformed lines."""
    records: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return records


# ---------------------------------------------------------------------------
# Metric aggregation
# ---------------------------------------------------------------------------

def _aggregate(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return per-connector metrics dict.

    Key: connector name (str)
    Value: {"total": int, "errors": int, "ms_values": list[float]}
    """
    result: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "errors": 0, "ms_values": []}
    )
    for r in records:
        connector = r.get("connector") or r.get("module") or "unknown"
        result[connector]["total"] += 1
        if (str(r.get("level", "")).upper() in _ERROR_LEVELS
                or r.get("ok") is False):
            result[connector]["errors"] += 1
        ms = r.get("duration_ms") or r.get("ms")
        if isinstance(ms, (int, float)) and ms >= 0:
            result[connector]["ms_values"].append(float(ms))
    return dict(result)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    sorted_v = sorted(values)
    idx = int((len(sorted_v) - 1) * pct / 100)
    return sorted_v[idx]


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------

def _detect_anomalies(
    connectors_7d: dict[str, dict[str, Any]],
    connectors_14d: dict[str, dict[str, Any]],
    session_counts: dict[str, int],
    quiet_threshold: int = 3,
) -> list[dict[str, Any]]:
    """Return list of anomaly dicts."""
    anomalies: list[dict[str, Any]] = []

    for connector, metrics in connectors_7d.items():
        total = metrics["total"]
        errors = metrics["errors"]
        error_rate = errors / total if total > 0 else 0.0

        # HIGH_ERROR_RATE
        if error_rate > _HIGH_ERROR_THRESHOLD:
            anomalies.append({
                "code": "HIGH_ERROR_RATE",
                "severity": "P1",
                "connector": connector,
                "message": f"Error rate {error_rate:.1%} > {_HIGH_ERROR_THRESHOLD:.0%} threshold",
                "error_rate": round(error_rate, 4),
            })

        # TREND_WORSENING (requires 14d baseline)
        if connector in connectors_14d:
            old = connectors_14d[connector]
            old_total = old["total"] - total
            old_errors = old["errors"] - errors
            if old_total > 0:
                old_rate = old_errors / old_total
                if (error_rate - old_rate) > _TREND_WORSENING_PP:
                    anomalies.append({
                        "code": "TREND_WORSENING",
                        "severity": "P2",
                        "connector": connector,
                        "message": (
                            f"7d error rate {error_rate:.1%} exceeds "
                            f"14d baseline {old_rate:.1%} by >{_TREND_WORSENING_PP*100:.0f}pp"
                        ),
                        "rate_7d": round(error_rate, 4),
                        "rate_14d": round(old_rate, 4),
                    })

    # QUIET_FAILURE: connector with 0 records in recent sessions
    for connector, n_sessions in session_counts.items():
        if n_sessions >= quiet_threshold and connectors_7d.get(connector, {}).get("total", 0) == 0:
            anomalies.append({
                "code": "QUIET_FAILURE",
                "severity": "P2",
                "connector": connector,
                "message": f"0 log records from '{connector}' across {n_sessions} recent sessions",
            })

    return anomalies


# ---------------------------------------------------------------------------
# Main digest computation
# ---------------------------------------------------------------------------

def build_digest(
    log_dir: Path = _LOG_DIR,
    lookback_days: int = 7,
    baseline_days: int = 14,
    window_days: int | None = None,
) -> dict[str, Any]:
    """Build and return a digest dict."""
    if window_days is not None:
        lookback_days = window_days
    log_dir = Path(log_dir)  # accept str or Path
    files = _discover_log_files(log_dir, lookback_days=max(lookback_days, baseline_days))
    cutoff_7d = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    records_7d: list[dict[str, Any]] = []
    records_14d: list[dict[str, Any]] = []
    # Approximate session counting: unique log files in 7d window = sessions
    session_files = [f for f in files if (_file_date(f) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff_7d]

    for f in files:
        recs = _parse_records(f)
        records_14d.extend(recs)
        file_date = _file_date(f)
        if file_date and file_date >= cutoff_7d:
            records_7d.extend(recs)

    conn_7d = _aggregate(records_7d)
    conn_14d = _aggregate(records_14d)

    # Session counts per connector (number of log files connector appeared in)
    session_counts: dict[str, int] = defaultdict(int)
    for f in session_files:
        seen: set[str] = set()
        for r in _parse_records(f):
            c = r.get("connector") or r.get("module") or "unknown"
            if c not in seen:
                session_counts[c] += 1
                seen.add(c)

    anomalies = _detect_anomalies(conn_7d, conn_14d, dict(session_counts))

    # Finalize per-connector output dict
    connectors: dict[str, Any] = {}
    for connector, metrics in conn_7d.items():
        total = metrics["total"]
        errors = metrics["errors"]
        error_rate = errors / total if total > 0 else 0.0
        connectors[connector] = {
            "total": total,
            "errors": errors,
            "error_rate": round(error_rate, 4),
            "p95_ms": _percentile(metrics["ms_values"], 95),
        }

    total_records = sum(m["total"] for m in conn_7d.values())
    total_errors = sum(m["errors"] for m in conn_7d.values())
    error_budget_pct = (total_errors / total_records * 100) if total_records > 0 else 0.0

    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "total_records": total_records,
        "total_errors": total_errors,
        "error_budget_pct": round(error_budget_pct, 2),
        "connectors": connectors,
        "anomalies": anomalies,
    }


def write_digest(digest: dict[str, Any]) -> None:
    """Atomically write digest to tmp/log_digest.json."""
    TMP_DIR.mkdir(exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=TMP_DIR, prefix=".log_digest-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(digest, fh, ensure_ascii=False, indent=2, default=str)
            fh.write("\n")
        out_file = TMP_DIR / "log_digest.json"
        os.replace(tmp_path, out_file)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> "argparse.Namespace":
    import argparse
    p = argparse.ArgumentParser(
        prog="log_digest.py",
        description="Aggregate structured JSONL logs into tmp/log_digest.json",
    )
    p.add_argument(
        "--lookback-days",
        dest="lookback_days",
        type=int,
        default=7,
        metavar="N",
        help="Lookback window for current metrics (default: 7)",
    )
    p.add_argument(
        "--baseline-days",
        dest="baseline_days",
        type=int,
        default=14,
        metavar="N",
        help="Baseline window for trend detection (default: 14)",
    )
    p.add_argument(
        "--log-dir",
        dest="log_dir",
        default=str(_LOG_DIR),
        metavar="PATH",
        help=f"JSONL log directory (default: {_LOG_DIR})",
    )
    p.add_argument("--json", action="store_true", help="Print digest JSON to stdout")
    p.add_argument(
        "--no-save",
        action="store_true",
        help="Print but do not write tmp/log_digest.json",
    )
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    log_dir = Path(args.log_dir)

    digest = build_digest(
        log_dir=log_dir,
        lookback_days=args.lookback_days,
        baseline_days=args.baseline_days,
    )

    if args.json:
        print(json.dumps(digest, indent=2, default=str))
    elif args.verbose:
        print(f"Records (7d): {digest['total_records']}")
        print(f"Errors  (7d): {digest['total_errors']}")
        print(f"Error budget: {digest['error_budget_pct']:.1f}%")
        for conn, m in digest["connectors"].items():
            p95 = f"{m['p95_ms']:.0f}ms" if m["p95_ms"] is not None else "N/A"
            print(f"  {conn:<20} records={m['total']} errors={m['errors']} p95={p95}")
        if digest["anomalies"]:
            print("\nAnomalies:")
            for a in digest["anomalies"]:
                print(f"  [{a['severity']}] {a['code']}: {a['message']}")
    else:
        print(
            f"[log_digest] {digest['total_records']} records, "
            f"error_budget={digest['error_budget_pct']:.1f}%, "
            f"anomalies={len(digest['anomalies'])}",
            file=sys.stderr,
        )

    if not args.no_save:
        try:
            write_digest(digest)
            if args.verbose:
                print(f"\n[log_digest] Written to {_DIGEST_FILE}", file=sys.stderr)
        except Exception as exc:
            print(f"[log_digest] ERROR writing digest: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
