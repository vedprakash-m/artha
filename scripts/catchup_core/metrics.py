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

__all__ = ["COMPLETE_GOAL", "MIN_RUNS", "P95_GATE_SECONDS", "TRACE_SCHEMA", "read_runs", "summarize", "to_trace"]

P95_GATE_SECONDS = 180.0         # PRD: full Catch-up under three minutes, provisional pilot gate (10.4)
COMPLETE_GOAL = 0.95             # PRD: at least 95 % complete Catch-ups
MIN_RUNS = 5                     # fewer runs than this are reported but never judged
TRACE_SCHEMA = "artha.trace.v1"      # OTLP/JSON-shaped; pinned so a convention change never alters old files
MONTHLY_BUDGET_USD = 50.0        # PRD: under $50 a month at the stated cadence (provisional pilot gate)


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


def estimate_cost(runs: Sequence[dict[str, Any]], pricing: dict[str, Any] | None) -> dict[str, Any]:
    """Cash-cost estimate from prompt and answer *sizes* (characters), never content.

    Assistant-mediated extraction reports no token counts, so this converts characters with a declared
    ``chars_per_token`` and the owner's dated prices.  It is an estimate and says so; without pricing it reports
    the sizes only.  ``runs_per_month`` is the cadence the monthly projection assumes (default 30).
    """
    finished = [r for r in runs if r.get("status") in ("complete", "degraded", "blocked")]
    prompt = sum(int(r.get("prompt_chars", 0)) for r in finished)
    answer = sum(int(r.get("answer_chars", 0)) for r in finished)
    out: dict[str, Any] = {"runs": len(finished), "prompt_chars": prompt, "answer_chars": answer}
    needed = ("input_usd_per_million_tokens", "output_usd_per_million_tokens", "as_of")
    if not finished or not pricing or any(k not in pricing for k in needed):
        out["estimate"] = ("unavailable: add pricing (input/output USD per million tokens and an as_of date) "
                           "to the config")
        return out
    per_token = float(pricing.get("chars_per_token", 4)) or 4.0
    per_run = (prompt / per_token * float(pricing["input_usd_per_million_tokens"])
               + answer / per_token * float(pricing["output_usd_per_million_tokens"])) / 1_000_000 / len(finished)
    monthly = per_run * float(pricing.get("runs_per_month", 30))
    out.update({"usd_per_run": round(per_run, 4), "projected_monthly_usd": round(monthly, 2),
                "within_monthly_budget": monthly <= MONTHLY_BUDGET_USD, "prices_as_of": str(pricing["as_of"]),
                "estimate": "approximate (characters converted to tokens; assistant-side usage is not visible)"})
    return out


def _hex(seed: str, length: int) -> str:
    import hashlib  # noqa: PLC0415
    return hashlib.sha256(seed.encode()).hexdigest()[:length]


def to_trace(entry: dict[str, Any], *, version: str = "0") -> dict[str, Any]:
    """One OpenTelemetry-shaped trace (OTLP/JSON resourceSpans) for a run, built from its metrics line.

    A root span for the Catch-up and one child span per stage that ran, laid out in sequence from the run's start.
    Attributes carry status, mode and counts only; no prompt, answer, subject or evidence content ever enters a trace,
    so exporting one is not a disclosure path (re-artha 6.6.5: content capture in traces is off).
    """
    catchup = str(entry.get("catchup_id", ""))
    trace_id = _hex("trace|" + catchup, 32)
    try:
        start = int(datetime.fromisoformat(str(entry["at"]).replace("Z", "+00:00")).timestamp() * 1_000_000_000)
    except (KeyError, ValueError):
        start = 0
    total = int(float(entry.get("total_seconds", 0)) * 1_000_000_000)
    root_id = _hex("span|" + catchup + "|root", 16)

    def attr(key: str, value: Any) -> dict[str, Any]:
        kind = "intValue" if isinstance(value, int) and not isinstance(value, bool) else (
            "boolValue" if isinstance(value, bool) else "stringValue")
        return {"key": key, "value": {kind: value if kind != "stringValue" else str(value)}}

    spans = [{
        "traceId": trace_id, "spanId": root_id, "name": "catchup", "kind": 1,
        "startTimeUnixNano": str(start), "endTimeUnixNano": str(start + total),
        "attributes": [attr("artha.status", entry.get("status", "")), attr("artha.mode", entry.get("mode", "")),
                       attr("artha.model_requests", int(entry.get("model_requests", 0))),
                       attr("artha.resumed", bool(entry.get("resumed", False)))],
        "status": {"code": 2 if entry.get("status") == "blocked" else 1},
    }]
    cursor = start
    for stage, seconds in (entry.get("stages") or {}).items():
        length = int(float(seconds) * 1_000_000_000)
        spans.append({
            "traceId": trace_id, "spanId": _hex(f"span|{catchup}|{stage}", 16), "parentSpanId": root_id,
            "name": f"stage.{str(stage).lower()}", "kind": 1, "startTimeUnixNano": str(cursor),
            "endTimeUnixNano": str(cursor + length), "attributes": [attr("artha.stage", stage)], "status": {"code": 1}})
        cursor += length
    return {"schema": TRACE_SCHEMA, "resourceSpans": [{
        "resource": {"attributes": [attr("service.name", "artha.catchup_core"), attr("service.version", version)]},
        "scopeSpans": [{"scope": {"name": "catchup_core"}, "spans": spans}]}]}
