"""Run performance: honest percentiles, no verdict on a tiny sample, never content (re-artha 10.4)."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from itertools import count

from catchup_core import runtime as rt
from catchup_core.metrics import MIN_RUNS, P95_GATE_SECONDS, read_runs, summarize


_RUN_IDS = count()


def run(seconds, status="complete", **extra):
    start = datetime(2026, 10, 22, tzinfo=UTC)
    return {"total_seconds": seconds, "status": status, "stages": {"FETCH": seconds / 2},
            'catchup_id': f'c{next(_RUN_IDS)}', 'resumed': False, 'at': start.isoformat(),
            'ended_at': (start + timedelta(seconds=seconds)).isoformat(), **extra}


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
    slow = summarize([run(60) for _ in range(MIN_RUNS - 1)] + [run(P95_GATE_SECONDS + 1)])
    assert slow["verdict"] == "misses the latency gate" and slow["slowest_stage"] == "FETCH"
    mixed = summarize([run(50) for _ in range(4)] + [run(50, "degraded")])
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
    assert json.loads(path.read_text().splitlines()[0])["schema"] == "artha.trace.v2"
    assert os.name == "nt" or oct(path.stat().st_mode & 0o777) == "0o600"


def test_logical_catchup_combines_pause_work_and_wait_without_counting_replays_twice():
    rows = [
        run(30, 'paused', catchup_id='one', mode='shadow', invocation_id='a', resumed=False, metric_sequence=1,
            at='2026-10-22T10:00:00Z', ended_at='2026-10-22T10:00:30Z', model_requests=2,
            prompt_chars=100, answer_chars=0),
        run(10, catchup_id='one', mode='shadow', invocation_id='b', resumed=True, metric_sequence=2,
            at='2026-10-22T10:05:00Z', ended_at='2026-10-22T10:05:10Z', model_requests=1,
            prompt_chars=50, answer_chars=20),
    ]
    rows += [dict(rows[1]), run(1, catchup_id='one', mode='shadow', invocation_id='replay', resumed=True,
        at='2026-10-22T11:00:00Z', ended_at='2026-10-22T11:00:01Z', model_requests=0)]
    rows.insert(1, dict(rows[0]))
    result = summarize(rows)
    assert result['runs'] == 1 and result['paused_runs'] == 0
    assert result['p95_seconds'] == 310 and result['p95_active_seconds'] == 40
    assert result['model_requests_per_run'] == 3 and result['invocations'] == 2
    from catchup_core.metrics import estimate_cost
    cost = estimate_cost(rows, {'input_usd_per_million_tokens': 1, 'output_usd_per_million_tokens': 1,
                                'as_of': '2026-10-22'})
    assert cost['runs'] == 1 and cost['prompt_chars'] == 150 and cost['answer_chars'] == 20


def test_truncated_resume_history_cannot_establish_a_latency_or_budget_pass():
    rows = [run(1, catchup_id=f'c{i}', resumed=True, at='2026-10-22T10:00:00Z',
                ended_at='2026-10-22T10:00:01Z') for i in range(MIN_RUNS)]
    result = summarize(rows)
    assert result['verdict'] == 'latency history is incomplete' and not result['meets_complete_goal']
    assert result['p95_seconds'] is None and result['incomplete_runs'] == MIN_RUNS
    from catchup_core.metrics import estimate_cost
    cost = estimate_cost(rows, {'input_usd_per_million_tokens': 1, 'output_usd_per_million_tokens': 1,
                                'as_of': '2026-10-22'})
    assert cost['within_monthly_budget'] is None and cost['history_complete'] is False


def test_paused_work_is_included_in_cost_even_before_completion():
    from catchup_core.metrics import estimate_cost
    cost = estimate_cost([run(5, 'paused', catchup_id='p', resumed=False, prompt_chars=4000,
        at='2026-10-22T10:00:00Z', ended_at='2026-10-22T10:00:05Z')], None)
    assert cost['runs'] == 1 and cost['prompt_chars'] == 4000 and cost['unfinished_runs'] == 1


def test_trace_spans_for_resumed_invocations_are_distinct_in_the_same_logical_trace():
    from catchup_core.metrics import to_trace
    entry = run(10, catchup_id='shared', mode='shadow', invocation_id='first')
    first = to_trace(entry)['resourceSpans'][0]['scopeSpans'][0]['spans']
    resumed = to_trace({**entry, 'invocation_id': 'second', 'resumed': True})['resourceSpans'][0]['scopeSpans'][0]['spans']
    assert first[0]['traceId'] == resumed[0]['traceId']
    assert {s['spanId'] for s in first}.isdisjoint({s['spanId'] for s in resumed})
    other = to_trace({**entry, 'mode': 'authoritative'})['resourceSpans'][0]['scopeSpans'][0]['spans']
    assert first[0]['traceId'] != other[0]['traceId']


def test_missing_middle_invocation_cannot_be_hidden_by_a_known_start_and_finish():
    rows = [run(5, 'paused', catchup_id='gap', metric_sequence=1),
            run(5, catchup_id='gap', resumed=True, metric_sequence=3)]
    assert summarize(rows)['verdict'] == 'latency history is incomplete'


def test_reading_recent_metrics_has_bounded_memory_for_a_large_torn_prefix(tmp_path):
    import tracemalloc
    directory = tmp_path / 'metrics'
    directory.mkdir()
    recent = [run(2), run(3)]
    (directory / '2026-10.jsonl').write_bytes(b'x' * (8 * 1024 * 1024) + b'\n' +
        b'\n'.join(json.dumps(row).encode() for row in recent) + b'\n')
    tracemalloc.start()
    try:
        found = read_runs(directory, limit=2)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert found == recent and peak < 2 * 1024 * 1024
    assert read_runs(directory, limit=0) == []


def test_non_finite_or_negative_metrics_and_prices_never_establish_a_pass(tmp_path):
    from catchup_core.metrics import estimate_cost
    directory = tmp_path / 'metrics'
    directory.mkdir()
    invalid = [{**run(1), 'total_seconds': float('nan')}, run(-1), {'total_seconds': True, 'status': 'complete'}]
    (directory / '2026-10.jsonl').write_text('\n'.join(json.dumps(row) for row in invalid))
    assert read_runs(directory) == []
    for invalid_price in (float('nan'), float('inf'), -1, True, 'invalid'):
        cost = estimate_cost([run(5)], {'input_usd_per_million_tokens': invalid_price,
            'output_usd_per_million_tokens': 1, 'as_of': '2026-10-22'})
        assert cost['estimate'].startswith('unavailable') and 'usd_per_run' not in cost


def test_pending_catchups_are_not_counted_as_completed_for_the_complete_ratio():
    rows = [run(10) for _ in range(MIN_RUNS)] + [run(1, 'paused')]
    result = summarize(rows)
    assert result['complete_ratio'] == 0.833 and not result['meets_complete_goal']


def test_huge_numeric_logs_and_invalid_limits_are_bounded(tmp_path):
    from catchup_core.metrics import estimate_cost
    import pytest
    directory = tmp_path / 'metrics'
    directory.mkdir()
    (directory / '2026-10.jsonl').write_text(json.dumps({**run(1), 'total_seconds': 10 ** 400}))
    assert read_runs(directory) == []
    for limit in (-1, True, 1.5):
        with pytest.raises(ValueError):
            read_runs(directory, limit=limit)
    assert estimate_cost([run(1)], {'input_usd_per_million_tokens': 10 ** 400,
        'output_usd_per_million_tokens': 1, 'as_of': '2026-10-22'})['estimate'].startswith('unavailable')


def test_legacy_partial_history_and_invalid_clocks_stay_unverified():
    rows = [run(1, 'paused', catchup_id='legacy'), run(2, catchup_id='legacy', resumed=True)]
    assert summarize(rows)['p95_seconds'] is None
    assert summarize([run(1, at='invalid', ended_at=None)])['p95_seconds'] is None
    standalone = {'total_seconds': 1, 'status': 'complete'}
    assert summarize([standalone])['verdict'] == 'latency history is incomplete'
    # An older single invocation has a usable inferred end, but no pause history is invented.
    assert summarize([run(2, ended_at=None)])['p95_seconds'] == 2


def test_oversized_log_records_and_overflowing_cost_estimates_are_unavailable(tmp_path):
    from catchup_core.metrics import estimate_cost
    directory = tmp_path / 'metrics'
    directory.mkdir()
    valid = run(1)
    (directory / '2026-10.jsonl').write_bytes(json.dumps(valid).encode() + b'\n' + b'x' * 65_537 + b'\n')
    assert read_runs(directory, limit=2) == [valid]
    cost = estimate_cost([run(1, prompt_chars=1000)], {'input_usd_per_million_tokens': 1e308,
        'output_usd_per_million_tokens': 1, 'chars_per_token': 1e-300, 'as_of': '2026-10-22'})
    assert cost['estimate'].startswith('unavailable') and 'usd_per_run' not in cost


def test_measurements_from_different_implementations_do_not_establish_a_pass():
    rows = [run(5, implementation_version='old') for _ in range(MIN_RUNS)]
    rows += [run(5, implementation_version='current')]
    result = summarize(rows)
    assert result['verdict'] == 'measurement versions differ' and not result['meets_complete_goal']
    assert result['p95_seconds'] is None and result['implementation_versions'] == ['current', 'old']


def test_health_latency_uses_the_current_code_cohort_and_cost_keeps_older_work(tmp_path):
    from catchup_core.commands import _performance
    from catchup_core.ledger import Ledger
    from test_release_regressions import CIPHER, KEY
    directory = tmp_path / 'metrics'
    directory.mkdir()
    rows = [run(1000, implementation_version='old', prompt_chars=1000)]
    rows += [run(5, implementation_version='current', prompt_chars=1000) for _ in range(MIN_RUNS)]
    (directory / '2026-10.jsonl').write_text('\n'.join(json.dumps(row) for row in rows))
    led = Ledger(tmp_path / 'ledger.db', id_key=KEY, cipher=CIPHER).open()
    try:
        led.migrate()
        report = _performance(led, tmp_path, implementation_version='current')
    finally:
        led.close()
    assert report['p95_seconds'] == 5 and report['runs'] == MIN_RUNS and report['excluded_other_versions'] == 1
    assert report['cost']['prompt_chars'] == 6000 and report['cost']['runs'] == 6


def test_invalid_direct_measurements_remain_unknown_instead_of_establishing_success():
    from catchup_core.metrics import estimate_cost
    rows = [{**run(1), 'prompt_chars': 10 ** 400}]
    summary = summarize(rows)
    assert summary['runs'] == 0 and summary['unknown_runs'] == 1
    assert estimate_cost(rows, None)['history_complete'] is False
