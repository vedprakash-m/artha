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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

__all__ = ["COMPLETE_GOAL", "MIN_RUNS", "P95_GATE_SECONDS", "TRACE_SCHEMA", "read_runs", "summarize", "to_trace"]

P95_GATE_SECONDS = 180.0         # PRD: full Catch-up under three minutes, provisional pilot gate (10.4)
COMPLETE_GOAL = 0.95             # PRD: at least 95 % complete Catch-ups
MIN_RUNS = 5                     # fewer runs than this are reported but never judged
TRACE_SCHEMA = "artha.trace.v2"      # invocation span IDs are distinct inside one logical Catch-up trace
MONTHLY_BUDGET_USD = 50.0        # PRD: under $50 a month at the stated cadence (provisional pilot gate)


def read_runs(directory: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    """Read a bounded tail of recent invocations; truncated sequences remain unverified when aggregated."""
    if type(limit) is not int or limit < 0:
        raise ValueError('metrics limit must be a nonnegative integer')
    runs: list[dict[str, Any]] = []
    paths = sorted(Path(directory).glob('*.jsonl'), reverse=True) if Path(directory).is_dir() else []
    for path in paths:
        if len(runs) >= limit:
            break
        with path.open('rb') as stream:
            end = stream.seek(0, 2)
            start = max(0, end - (limit - len(runs)) * 65_536)
            stream.seek(start)
            tail = stream.read(end - start)
        if start:
            tail = tail.partition(b'\n')[2]  # never parse a cut-off prefix as a whole record
        for line in reversed(tail.splitlines()):
            if len(line) > 65_536:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and _valid_run(row):
                runs.append(row)
                if len(runs) >= limit:
                    break
    return list(reversed(runs))


def _nonnegative_number(value: Any) -> bool:
    try:
        return (not isinstance(value, bool) and isinstance(value, int | float)
                and math.isfinite(value) and value >= 0)
    except OverflowError:
        return False


def _valid_run(row: dict[str, Any]) -> bool:
    stages = row.get('stages', {})
    if not (_nonnegative_number(row.get('total_seconds')) and isinstance(stages, dict)
            and all(_nonnegative_number(v) for v in stages.values())
            and all(type(row.get(k, 0)) is int and _nonnegative_number(row.get(k, 0))
                    for k in ('model_requests', 'prompt_chars', 'answer_chars'))):
        return False
    if 'actual_usage_verified' in row and not isinstance(row['actual_usage_verified'], bool):
        return False
    for token_key in ('actual_prompt_tokens', 'actual_completion_tokens'):
        if token_key in row and (type(row[token_key]) is not int or row[token_key] < 0):
            return False
    if 'actual_cost_usd' in row:
        return _nonnegative_number(row['actual_cost_usd'])
    return True


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def _instant(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
    except ValueError:
        return None


def _logical_runs(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """One Catch-up includes its paused invocations; replay after a terminal receipt adds no sample."""
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for index, raw in enumerate(runs):
        entry = raw if _valid_run(raw) else {
            **{k: raw.get(k) for k in ('catchup_id', 'mode', 'implementation_version')},
            'status': 'unknown', 'total_seconds': 0, 'measurement_complete': False, 'resumed': True}
        key = (str(entry.get('mode', 'unknown')), str(entry.get('implementation_version', 'legacy')),
               str(entry.get('catchup_id') or f'unlinked:{index}'))
        grouped.setdefault(key, []).append(entry)
    output: list[dict[str, Any]] = []
    for entries in grouped.values():
        seen: set[str] = set()
        parts = []
        for entry in entries:
            identity = str(entry.get('invocation_id') or json.dumps(entry, sort_keys=True))
            if identity in seen:
                continue
            seen.add(identity)
            parts.append(entry)
            if entry.get('status') in {'complete', 'degraded', 'blocked'}:
                break
        first, last = parts[0], parts[-1]
        active = sum(float(r['total_seconds']) for r in parts)
        start = _instant(first.get('at'))
        finish = _instant(last.get('ended_at'))
        if finish is None and (end_start := _instant(last.get('at'))) is not None:
            finish = end_start + timedelta(seconds=float(last['total_seconds']))
        complete = bool(first.get('catchup_id') and first.get('resumed') is False
                        and start is not None and finish is not None and finish >= start
                        and all(p.get('measurement_complete', True) for p in parts))
        if any(p.get('metric_sequence') is not None for p in parts):
            complete = complete and all(type(p.get('metric_sequence')) is int and p['metric_sequence'] == i
                                        for i, p in enumerate(parts, 1))
        else:
            complete = complete and len(parts) == 1  # old resumed telemetry cannot prove an unbroken sequence
        stages: dict[str, float] = {}
        for part in parts:
            for stage, seconds in (part.get('stages') or {}).items():
                stages[stage] = stages.get(stage, 0) + float(seconds)
        elapsed = max(active, (finish - start).total_seconds()) if start and finish else active
        actual_verified = bool(parts) and all(bool(p.get('actual_usage_verified', False)) for p in parts)
        actual_prompt = sum(int(p.get('actual_prompt_tokens', 0)) for p in parts)
        actual_completion = sum(int(p.get('actual_completion_tokens', 0)) for p in parts)
        actual_cost = sum(float(p.get('actual_cost_usd', 0.0)) for p in parts)
        output.append({**last, 'total_seconds': elapsed, 'active_seconds': active,
            'invocations': len(parts), 'history_complete': complete, 'stages': stages,
            **{field: sum(int(p.get(field, 0)) for p in parts)
               for field in ('model_requests', 'prompt_chars', 'answer_chars')},
            'actual_prompt_tokens': actual_prompt,
            'actual_completion_tokens': actual_completion,
            'actual_cost_usd': round(actual_cost, 4),
            'actual_usage_verified': actual_verified})
    return output


def summarize(runs: Sequence[dict[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    """p50/p95 over finished runs, the complete-run ratio, and a verdict that refuses to judge a tiny sample."""
    logical = _logical_runs(runs)
    finished = [r for r in logical if r.get("status") in ("complete", "degraded", "blocked")]
    if not finished:
        return {"runs": 0, "verdict": "no finished runs recorded yet",
                "paused_runs": sum(r.get('status') == 'paused' for r in logical),
                'unknown_runs': sum(r.get('status') != 'paused' for r in logical)}
    seconds = [float(r["total_seconds"]) for r in finished]
    complete = sum(1 for r in finished if r["status"] == "complete") / len(logical)
    slowest: dict[str, float] = {}
    for r in finished:
        for stage, value in (r.get("stages") or {}).items():
            slowest[stage] = max(slowest.get(stage, 0.0), float(value))
    p95 = _percentile(seconds, 0.95)
    incomplete = sum(not r['history_complete'] for r in finished)
    versions = {str(r.get('implementation_version', 'legacy')) for r in logical}
    mixed = len(versions) > 1
    verdict = ("measurement versions differ" if mixed else "latency history is incomplete" if incomplete else
               "too few runs to judge" if len(finished) < MIN_RUNS else
               "meets the latency gate" if p95 <= P95_GATE_SECONDS else "misses the latency gate")
    return {
        "runs": len(finished), "paused_runs": sum(r.get('status') == 'paused' for r in logical),
        'unknown_runs': len(logical) - len(finished) - sum(r.get('status') == 'paused' for r in logical),
        "p50_seconds": round(_percentile(seconds, 0.5), 2) if not incomplete and not mixed else None,
        "p95_seconds": round(p95, 2) if not incomplete and not mixed else None,
        "p95_active_seconds": round(_percentile([float(r['active_seconds']) for r in finished], 0.95), 2),
        "incomplete_runs": incomplete, "invocations": sum(int(r['invocations']) for r in logical),
        'implementation_versions': sorted(versions),
        "complete_ratio": round(complete, 3),
        "meets_complete_goal": not incomplete and not mixed and len(finished) >= MIN_RUNS and complete >= COMPLETE_GOAL,
        "slowest_stage": max(slowest, key=lambda k: slowest[k]) if slowest else None,
        "model_requests_per_run": round(sum(int(r.get("model_requests", 0)) for r in finished) / len(finished), 2),
        "verdict": verdict, "as_of": now.isoformat() if now else None,
    }


def estimate_cost(runs: Sequence[dict[str, Any]], pricing: dict[str, Any] | None) -> dict[str, Any]:
    """Cash-cost estimate from prompt and answer *sizes* (characters), never content.

    Assistant-mediated extraction reports no token counts, so this converts characters with a declared
    ``chars_per_token`` and the owner's dated prices.  It is an estimate and says so; without pricing it reports
    the sizes only.  ``runs_per_month`` is the cadence the monthly projection assumes (default 30).
    When actual provider usage is verified, it is reported alongside the character estimate.
    """
    finished = _logical_runs(runs)  # paused requests have already incurred observable work too
    prompt = sum(int(r.get("prompt_chars", 0)) for r in finished)
    answer = sum(int(r.get("answer_chars", 0)) for r in finished)
    complete = all(r['history_complete'] for r in finished)
    actual_verified = bool(finished) and all(r.get('actual_usage_verified') is True for r in finished)
    actual_prompt = sum(int(r.get("actual_prompt_tokens", 0)) for r in finished)
    actual_completion = sum(int(r.get("actual_completion_tokens", 0)) for r in finished)
    actual_cost = sum(float(r.get("actual_cost_usd", 0.0)) for r in finished)

    out: dict[str, Any] = {"runs": len(finished), "prompt_chars": prompt, "answer_chars": answer,
        "unfinished_runs": sum(r.get('status') == 'paused' for r in finished), 'history_complete': complete,
        'actual_usage_verified': actual_verified, 'budget_enforced': False}
    if actual_verified:
        out["actual_prompt_tokens"] = actual_prompt
        out["actual_completion_tokens"] = actual_completion
        out["actual_cost_usd"] = round(actual_cost, 4)
        if finished:
            out["actual_usd_per_run"] = round(actual_cost / len(finished), 4)

    needed = ("input_usd_per_million_tokens", "output_usd_per_million_tokens", "as_of")
    if not finished or not pricing or any(k not in pricing for k in needed):
        out["estimate"] = ("unavailable: add pricing (input/output USD per million tokens and an as_of date) "
                           "to the config")
        return out
    quantities = [pricing[k] for k in needed[:2]] + [pricing.get('chars_per_token', 4),
                                                   pricing.get('runs_per_month', 30)]
    if (not all(_nonnegative_number(v) for v in quantities)
            or float(quantities[2]) <= 0 or float(quantities[3]) <= 0):
        out['estimate'] = 'unavailable: pricing and cadence must be finite valid nonnegative quantities'
        return out
    per_token = float(pricing.get("chars_per_token", 4))
    per_run = (prompt / per_token * float(pricing["input_usd_per_million_tokens"])
               + answer / per_token * float(pricing["output_usd_per_million_tokens"])) / 1_000_000 / len(finished)
    monthly = per_run * float(pricing.get("runs_per_month", 30))
    if not math.isfinite(monthly):
        out['estimate'] = 'unavailable: pricing calculation exceeded its finite range'
        return out
    out.update({"usd_per_run": round(per_run, 4), "projected_monthly_usd": round(monthly, 2),
                "within_monthly_budget": monthly <= MONTHLY_BUDGET_USD if complete else None,
                "prices_as_of": str(pricing["as_of"]),
                "estimate": ("approximate (characters converted to tokens; assistant-side usage is not visible)"
                             if complete else "approximate for recorded sizes only; invocation history is incomplete")})
    if actual_verified:
        actual_monthly = round((actual_cost / len(finished)) * float(pricing.get("runs_per_month", 30)), 2)
        out["actual_projected_monthly_usd"] = actual_monthly
        out["actual_within_monthly_budget"] = actual_monthly <= MONTHLY_BUDGET_USD if complete else None
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
    trace_id = _hex("trace|" + str(entry.get('mode', 'unknown')) + '|' + catchup, 32)
    try:
        start = int(datetime.fromisoformat(str(entry["at"]).replace("Z", "+00:00")).timestamp() * 1_000_000_000)
    except (KeyError, ValueError):
        start = 0
    total = int(float(entry.get("total_seconds", 0)) * 1_000_000_000)
    invocation = str(entry.get('invocation_id') or entry.get('at', ''))
    root_id = _hex("span|" + catchup + '|' + invocation + "|root", 16)

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
            "traceId": trace_id, "spanId": _hex(f"span|{catchup}|{invocation}|{stage}", 16), "parentSpanId": root_id,
            "name": f"stage.{str(stage).lower()}", "kind": 1, "startTimeUnixNano": str(cursor),
            "endTimeUnixNano": str(cursor + length), "attributes": [attr("artha.stage", stage)], "status": {"code": 1}})
        cursor += length
    return {"schema": TRACE_SCHEMA, "resourceSpans": [{
        "resource": {"attributes": [attr("service.name", "artha.catchup_core"), attr("service.version", version)]},
        "scopeSpans": [{"scope": {"name": "catchup_core"}, "spans": spans}]}]}
