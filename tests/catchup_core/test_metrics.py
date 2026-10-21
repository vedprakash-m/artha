"""Run performance: honest percentiles, no verdict on a tiny sample, never content (re-artha 10.4)."""
from __future__ import annotations

import json
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
    assert len(path.read_text().splitlines()) == 2 and oct(path.stat().st_mode & 0o777) == "0o600"
