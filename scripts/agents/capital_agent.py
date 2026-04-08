#!/usr/bin/env python3
# pii-guard: strict — financial data processed here; no raw values in logs
"""
scripts/agents/capital_agent.py — CapitalAgent pre-compute (EAR-3, §5.4).

Reads state/finance.md (historical financial context) and
~/.artha-local/cashflow.db (projection time-series), computes a 90-day
deterministic cash-flow projection, and writes a LLM-readable summary
to state/finance_forecast.md.

All arithmetic is done in Python — NOT inferred by the LLM.
The LLM's role is only to compose the proposal from the pre-computed figures.

Invoked by:
    python scripts/agents/capital_agent.py
    (or via agent_scheduler.py --tick on cron)

Exits 0 on success, 1 on error (heartbeat written either way).

SQLite schema (cashflow.db):
    cashflow_entries(id INTEGER PRIMARY KEY AUTOINCREMENT,
                     date TEXT NOT NULL, category TEXT NOT NULL,
                     amount REAL NOT NULL,
                     type TEXT NOT NULL CHECK(type IN ('fixed','variable','projected')),
                     source_ref TEXT, created_at TEXT)
    projections(id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL, projection_date TEXT NOT NULL,
                category TEXT NOT NULL, projected_amount REAL NOT NULL,
                confidence TEXT NOT NULL CHECK(confidence IN ('HIGH','MEDIUM','LOW','INSUFFICIENT_DATA')),
                data_points_used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT)

Confidence thresholds (§8.4 / R10):
    Variable categories: ≥6 months of historical data points → HIGH confidence
    Fixed/recurring categories: ≥3 months → HIGH confidence
    Below threshold: flag with ⚠ LOW-confidence or INSUFFICIENT_DATA

State files written:
    state/finance_forecast.md  — LLM-readable 90-day cash-flow forecast
    tmp/capital_last_run.json  — EAR-8 heartbeat

Ref: specs/prd-reloaded.md §5.4, §6.4, §8.4, §9.1
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SCRIPTS_DIR.parent
_LOCAL_DIR = Path.home() / ".artha-local"
_STATE_DIR = _REPO_ROOT / "state"
_TMP_DIR = _REPO_ROOT / "tmp"

_DB_PATH = _LOCAL_DIR / "cashflow.db"
_STATE_FILE = _STATE_DIR / "finance_forecast.md"
_SOURCE_FILE = _STATE_DIR / "finance.md"
_SENTINEL = _LOCAL_DIR / ".capital_writing"
_HEARTBEAT = _TMP_DIR / "capital_last_run.json"

_PROJECTION_DAYS = 90
_MIN_MONTHS_VARIABLE = 6   # §8.4 R10: variable categories
_MIN_MONTHS_FIXED = 3      # §8.4 R10: fixed/recurring categories
_VARIABLE_CATEGORIES = {"dining", "shopping", "entertainment", "groceries", "misc"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_heartbeat(status: str, records_written: int, trace_id: str) -> None:
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "domain": "capital",
        "session_trace_id": trace_id,
        "timestamp_utc": _now_utc(),
        "status": status,
        "records_written": records_written,
    }
    _HEARTBEAT.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _open_db() -> sqlite3.Connection:
    """Open cashflow.db with WAL mode and busy_timeout (A2.2 blanket policy)."""
    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cashflow_entries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT    NOT NULL,
            category    TEXT    NOT NULL,
            amount      REAL    NOT NULL,
            type        TEXT    NOT NULL CHECK(type IN ('fixed','variable','projected')),
            source_ref  TEXT,
            created_at  TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ce_date ON cashflow_entries(date)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projections (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date            TEXT NOT NULL,
            projection_date     TEXT NOT NULL,
            category            TEXT NOT NULL,
            projected_amount    REAL NOT NULL,
            confidence          TEXT NOT NULL
                                CHECK(confidence IN ('HIGH','MEDIUM','LOW','INSUFFICIENT_DATA')),
            data_points_used    INTEGER NOT NULL DEFAULT 0,
            created_at          TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_proj_run ON projections(run_date)")
    conn.commit()
    return conn


def _parse_finance_md(finance_path: Path) -> list[dict[str, Any]]:
    """Extract structured transaction hints from state/finance.md.

    Looks for simple table rows or YAML blocks with amount/category/type fields.
    Returns list of {date, category, amount, type, source_ref} dicts.
    This is a best-effort parser — unrecognised formats are silently skipped.
    """
    if not finance_path.exists():
        return []

    entries: list[dict[str, Any]] = []
    text = finance_path.read_text(encoding="utf-8", errors="replace")

    # Pattern: | YYYY-MM-DD | category | amount | type |
    table_row_re = re.compile(
        r"\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]+)\s*\|\s*\$?([\d,]+\.?\d*)\s*\|\s*(fixed|variable|projected)\s*\|",
        re.IGNORECASE,
    )
    for m in table_row_re.finditer(text):
        try:
            entries.append({
                "date": m.group(1),
                "category": m.group(2).strip().lower(),
                "amount": float(m.group(3).replace(",", "")),
                "type": m.group(4).lower(),
                "source_ref": f"state/finance.md",
            })
        except ValueError:
            continue

    return entries


def _compute_projection(
    conn: sqlite3.Connection, today: date
) -> tuple[list[dict[str, Any]], list[str]]:
    """Compute 90-day projections per category using historical averages.

    Returns (projection_rows, confidence_warnings).
    """
    # Load 180 days of history for projection confidence assessment
    cutoff = (today - timedelta(days=180)).isoformat()
    rows = conn.execute(
        "SELECT date, category, amount, type FROM cashflow_entries WHERE date >= ?",
        (cutoff,),
    ).fetchall()

    # Group by category
    by_category: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
    for row_date, cat, amt, rtype in rows:
        by_category[cat].append((row_date, amt, rtype))

    projections: list[dict[str, Any]] = []
    warnings: list[str] = []
    run_date_str = today.isoformat()
    now_str = _now_utc()

    for cat, history in by_category.items():
        is_variable = cat in _VARIABLE_CATEGORIES
        min_months = _MIN_MONTHS_VARIABLE if is_variable else _MIN_MONTHS_FIXED

        # Count unique months represented
        months_seen = len({h[0][:7] for h in history})
        data_points = len(history)

        if months_seen < min_months:
            confidence = "INSUFFICIENT_DATA"
            warn_msg = (
                f"⚠ Low-confidence projection — {months_seen} of {min_months} required "
                f"months of data for `{cat}`."
            )
            warnings.append(warn_msg)
        elif months_seen < min_months + 1:
            confidence = "LOW"
        elif months_seen < min_months + 3:
            confidence = "MEDIUM"
        else:
            confidence = "HIGH"

        # Average monthly amount
        avg_monthly = sum(h[1] for h in history) / max(1, months_seen)

        # Project for each of the next 90 days (monthly granularity — one record per category)
        for offset_months in range(0, (_PROJECTION_DAYS // 30) + 1):
            proj_date = today + timedelta(days=offset_months * 30)
            projections.append({
                "run_date": run_date_str,
                "projection_date": proj_date.isoformat(),
                "category": cat,
                "projected_amount": round(avg_monthly, 2),
                "confidence": confidence,
                "data_points_used": data_points,
                "created_at": now_str,
            })

    return projections, warnings


def _write_state_file(
    today: str,
    projections: list[dict[str, Any]],
    warnings: list[str],
    run_trace: str,
) -> None:
    """Write LLM-readable state/finance_forecast.md."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Build 90-day projection table (one row per category, next month's projected value)
    projection_lines = []
    seen_cats: dict[str, dict[str, Any]] = {}
    for p in projections:
        cat = p["category"]
        if cat not in seen_cats:
            seen_cats[cat] = p

    if seen_cats:
        projection_lines.append("| Category | Projected Monthly | Confidence | Data Points |")
        projection_lines.append("|----------|------------------|------------|-------------|")
        for cat, p in sorted(seen_cats.items()):
            projection_lines.append(
                f"| {cat} | ${p['projected_amount']:,.2f} | {p['confidence']} | {p['data_points_used']} |"
            )
    else:
        projection_lines.append("_No projection data available — import transactions to initialize._")

    # Liquidity alerts (INSUFFICIENT_DATA categories)
    alerts = [p for p in projections if p["confidence"] == "INSUFFICIENT_DATA"]
    alert_lines = []
    for w in warnings:
        alert_lines.append(f"- {w}")
    if not alert_lines:
        alert_lines.append("- No liquidity alerts — all categories have sufficient data.")

    # Source citations (all categories that have projection data)
    source_lines = []
    for cat in sorted(seen_cats.keys()):
        source_lines.append(
            f"- source: state/finance.md — category: `{cat}`, "
            f"confidence: {seen_cats[cat]['confidence']}"
        )
    if not source_lines:
        source_lines.append("- source: state/finance.md (no parseable transaction data found)")

    content = f"""# Capital Forecast
date: {today}

## 90-Day Projection
{chr(10).join(projection_lines)}

## Liquidity Alerts
{chr(10).join(alert_lines)}

## Proposed Actions
_No actions proposed — Capital proposals require explicit user review per §6.4._
_Amount-confirmation gate active: any proposal >$200 requires typed-amount approval._

## Source Citations
confidence: {_overall_confidence(seen_cats)}
{chr(10).join(source_lines)}
- generated_at: {_now_utc()}
- session_trace_id: {run_trace}
"""
    _STATE_FILE.write_text(content, encoding="utf-8")


def _overall_confidence(seen_cats: dict[str, dict[str, Any]]) -> str:
    if not seen_cats:
        return "INSUFFICIENT_DATA"
    levels = [p["confidence"] for p in seen_cats.values()]
    if "INSUFFICIENT_DATA" in levels:
        return "LOW"
    if "LOW" in levels:
        return "LOW"
    if "MEDIUM" in levels:
        return "MEDIUM"
    return "HIGH"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    today = date.today()
    today_str = today.isoformat()
    iso_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    trace_id = f"pre-compute-capital-{iso_ts}"

    # Acquire sentinel (pipeline.py will skip DB reads while this exists)
    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    _SENTINEL.write_text(_now_utc(), encoding="utf-8")

    try:
        conn = _open_db()

        # Ingest from state/finance.md
        finance_entries = _parse_finance_md(_SOURCE_FILE)
        records_written = 0
        if finance_entries:
            with conn:
                for entry in finance_entries:
                    conn.execute(
                        """INSERT OR IGNORE INTO cashflow_entries
                           (date, category, amount, type, source_ref, created_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            entry["date"],
                            entry["category"],
                            entry["amount"],
                            entry["type"],
                            entry["source_ref"],
                            _now_utc(),
                        ),
                    )
                    records_written += 1

        # Compute projections
        projections, warnings = _compute_projection(conn, today)

        # Store projections (replace today's run)
        if projections:
            with conn:
                conn.execute("DELETE FROM projections WHERE run_date = ?", (today_str,))
                conn.executemany(
                    """INSERT INTO projections
                       (run_date, projection_date, category, projected_amount,
                        confidence, data_points_used, created_at)
                       VALUES (:run_date, :projection_date, :category, :projected_amount,
                               :confidence, :data_points_used, :created_at)""",
                    projections,
                )
                records_written += len(projections)

        conn.close()

        _write_state_file(today_str, projections, warnings, trace_id)
        _write_heartbeat("success", records_written, trace_id)
        print(f"✓ CapitalAgent: projections={len(projections)}, warnings={len(warnings)}, "
              f"records={records_written}")
        return 0

    except Exception as exc:
        print(f"⛔ CapitalAgent failed: {exc}", file=sys.stderr)
        _write_heartbeat("error", 0, trace_id)
        return 1
    finally:
        if _SENTINEL.exists():
            _SENTINEL.unlink()


if __name__ == "__main__":
    sys.exit(main())
