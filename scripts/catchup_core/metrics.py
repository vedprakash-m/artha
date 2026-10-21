"""Per-run performance records and the latency gates they feed (re-artha 10.4, 10.7, 11.2).

Every Catch-up appends one line to ``~/.artha-local/catchup_core/metrics/YYYY-MM.jsonl``: its status, wall-clock
seconds, seconds per stage, and how many model requests it made.  No content is stored, so the file is not a
disclosure path.  ``summarize`` turns the recent runs into the numbers the release scorecard needs (p50 and p95
against the provisional <3 minute gate, share of complete runs against the >=95 % goal) and says plainly when there
are too few runs to mean anything.
"""
from __future__ import annotations

import json
import math
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = ["COMPLETE_GOAL", "MIN_RUNS", "P95_GATE_SECONDS", "read_runs", "summarize"]

P95_GATE_SECONDS = 180.0         # PRD: full Catch-up under three minutes, provisional pilot gate (10.4)
COMPLETE_GOAL = 0.95             # PRD: at least 95 % complete Catch-ups
MIN_RUNS = 5                     # fewer runs than this are reported but never judged


def read_runs(directory: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    """Most recent runs first-in-order, skipping unreadable lines (a torn write must not hide the rest)."""
    runs: list[dict[str, Any]] = []
    for path in sorted(Path(directory).glob("*.jsonl")) if Path(directory).is_dir() else []:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("total_seconds"), int | float):
                runs.append(row)
    return runs[-limit:]


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def summarize(runs: Sequence[dict[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    """p50/p95 over finished runs, the complete-run ratio, and a verdict that refuses to judge a tiny sample."""
    finished = [r for r in runs if r.get("status") in ("complete", "degraded", "blocked")]
    if not finished:
        return {"runs": 0, "verdict": "no finished runs recorded yet", "paused_runs": len(runs)}
    seconds = [float(r["total_seconds"]) for r in finished]
    complete = sum(1 for r in finished if r["status"] == "complete") / len(finished)
    slowest: dict[str, float] = {}
    for r in finished:
        for stage, value in (r.get("stages") or {}).items():
            slowest[stage] = max(slowest.get(stage, 0.0), float(value))
    p95 = _percentile(seconds, 0.95)
    verdict = ("too few runs to judge" if len(finished) < MIN_RUNS else
               "meets the latency gate" if p95 <= P95_GATE_SECONDS else "misses the latency gate")
    return {
        "runs": len(finished), "paused_runs": len(runs) - len(finished),
        "p50_seconds": round(_percentile(seconds, 0.5), 2), "p95_seconds": round(p95, 2),
        "complete_ratio": round(complete, 3),
        "meets_complete_goal": len(finished) >= MIN_RUNS and complete >= COMPLETE_GOAL,
        "slowest_stage": max(slowest, key=lambda k: slowest[k]) if slowest else None,
        "model_requests_per_run": round(sum(int(r.get("model_requests", 0)) for r in finished) / len(finished), 2),
        "verdict": verdict, "as_of": now.isoformat() if now else None,
    }
