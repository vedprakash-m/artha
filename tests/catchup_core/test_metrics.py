"""Run performance: honest percentiles, no verdict on a tiny sample, never content (re-artha 10.4)."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from catchup_core import runtime as rt
from catchup_core.metrics import MIN_RUNS, P95_GATE_SECONDS, read_runs, summarize


def run(seconds, status="complete", **extra):
    return {"total_seconds": seconds, "status": status, "stages": {"FETCH": seconds / 2}, **extra}


def test_no_finished_runs_says_so_and_counts_paused_ones():
    assert summarize([])["verdict"] == "no finished runs recorded yet"
    only_paused = summarize([run(1, "paused")])
    assert only_paused["runs"] == 0 and only_paused["paused_runs"] == 1


def test_a_tiny_sample_is_reported_but_never_judged():
    result = summarize([run(10), run(20), run(30)])
    assert result["verdict"] == "too few runs to judge" and result["p50_seconds"] == 20 and result["runs"] == 3
    assert result["meets_complete_goal"] is False


def test_p95_against_the_three_minute_gate_and_the_complete_ratio():
    fast = summarize([run(60 + i) for i in range(MIN_RUNS)])
    assert fast["verdict"] == "meets the latency gate" and fast["meets_complete_goal"] is True
    slow = summarize([run(60)] * (MIN_RUNS - 1) + [run(P95_GATE_SECONDS + 1)])
    assert slow["verdict"] == "misses the latency gate" and slow["slowest_stage"] == "FETCH"
    mixed = summarize([run(50)] * 4 + [run(50, "degraded")])
    assert mixed["complete_ratio"] == 0.8 and mixed["meets_complete_goal"] is False
    assert summarize([run(5, model_requests=3), run(5, model_requests=1)])["model_requests_per_run"] == 2.0


def test_read_runs_skips_torn_lines_and_keeps_the_most_recent(tmp_path):
    (tmp_path / "2026-09.jsonl").write_text(json.dumps(run(1)) + "\n")
    (tmp_path / "2026-10.jsonl").write_text(json.dumps(run(2)) + "\n{torn\n" + json.dumps({"no": "seconds"}) + "\n"
                                            + json.dumps(run(3)) + "\n")
    assert [r["total_seconds"] for r in read_runs(tmp_path)] == [1, 2, 3]
    assert [r["total_seconds"] for r in read_runs(tmp_path, limit=1)] == [3]
    assert read_runs(tmp_path / "missing") == []


def test_runtime_writer_appends_private_monthly_lines(tmp_path):
    write = rt._metrics_writer(tmp_path, lambda: datetime(2026, 10, 20, tzinfo=UTC))
    write({"total_seconds": 1.5, "status": "complete"})
    write({"total_seconds": 2.5, "status": "degraded"})
    path = tmp_path / "metrics" / "2026-10.jsonl"
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    assert os.name == "nt" or oct(path.stat().st_mode & 0o777) == "0o600"


def test_cost_needs_pricing_and_never_pretends_to_be_exact():
    from catchup_core.metrics import MONTHLY_BUDGET_USD, estimate_cost
    runs = [run(10, prompt_chars=40_000, answer_chars=4_000) for _ in range(4)]
    none = estimate_cost(runs, None)
    assert none["prompt_chars"] == 160_000 and none["estimate"].startswith("unavailable")
    assert "usd_per_run" not in estimate_cost(runs, {"input_usd_per_million_tokens": 1})
    priced = estimate_cost(runs, {"input_usd_per_million_tokens": 3.0, "output_usd_per_million_tokens": 15.0,
                                  "as_of": "2026-10-01"})
    # 10,000 prompt tokens and 1,000 answer tokens per run: $0.03 + $0.015
    assert priced["usd_per_run"] == 0.045 and priced["projected_monthly_usd"] == 1.35
    assert priced["within_monthly_budget"] is True and priced["prices_as_of"] == "2026-10-01"
    assert "approximate" in priced["estimate"]
    huge = estimate_cost(runs, {"input_usd_per_million_tokens": 3000, "output_usd_per_million_tokens": 15000,
                                "as_of": "x", "runs_per_month": 60, "chars_per_token": 2})
    assert huge["within_monthly_budget"] is False and huge["projected_monthly_usd"] > MONTHLY_BUDGET_USD
    assert estimate_cost([], {"input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 1,
                              "as_of": "x"})["estimate"].startswith("unavailable")


def test_trace_is_otlp_shaped_sequential_and_free_of_content():
    from catchup_core.metrics import TRACE_SCHEMA, to_trace
    entry = {"catchup_id": "cu_1", "mode": "shadow", "status": "degraded", "at": "2026-10-20T10:00:00Z",
             "total_seconds": 3.0, "stages": {"FETCH": 1.0, "EXTRACT": 2.0}, "model_requests": 2, "resumed": False,
             "prompt_chars": 999}
    trace = to_trace(entry, version="0.1.0")
    assert trace["schema"] == TRACE_SCHEMA
    spans = trace["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root, fetch, extract = spans
    assert root["name"] == "catchup" and fetch["name"] == "stage.fetch" and fetch["parentSpanId"] == root["spanId"]
    assert {s["traceId"] for s in spans} == {root["traceId"]} and len(root["traceId"]) == 32 and len(root["spanId"]) == 16
    assert int(fetch["endTimeUnixNano"]) == int(extract["startTimeUnixNano"])                 # stages run in sequence
    assert int(root["endTimeUnixNano"]) - int(root["startTimeUnixNano"]) == 3_000_000_000
    keys = {a["key"] for s in spans for a in s["attributes"]}
    assert "artha.status" in keys and not any("prompt" in k or "text" in k for k in keys)
    assert trace["resourceSpans"][0]["resource"]["attributes"][1]["value"]["stringValue"] == "0.1.0"
    assert to_trace(entry) == to_trace(entry)                                                  # deterministic IDs
    assert to_trace({**entry, "catchup_id": "cu_2"})["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"] != root["traceId"]


def test_trace_tolerates_a_bad_timestamp_and_marks_blocked_runs_as_errors():
    from catchup_core.metrics import to_trace
    blocked = to_trace({"catchup_id": "x", "status": "blocked", "at": "nope", "total_seconds": 1, "stages": {}})
    root = blocked["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert root["startTimeUnixNano"] == "0" and root["status"]["code"] == 2


def test_runtime_trace_writer_appends_private_monthly_lines(tmp_path):
    from datetime import UTC, datetime
    write = rt._trace_writer(tmp_path, lambda: datetime(2026, 10, 20, tzinfo=UTC))
    write({"catchup_id": "cu_1", "status": "complete", "at": "2026-10-20T10:00:00Z", "total_seconds": 1, "stages": {"FETCH": 1}})
    path = tmp_path / "traces" / "2026-10.jsonl"
    assert json.loads(path.read_text().splitlines()[0])["schema"] == "artha.trace.v1"
    assert os.name == "nt" or oct(path.stat().st_mode & 0o777) == "0o600"
