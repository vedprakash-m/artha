"""tests/ext_agents/test_metrics_writer.py — EA-10a metrics JSONL writer tests."""
from __future__ import annotations

import json
from pathlib import Path


_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")

from lib.metrics_writer import (  # type: ignore
    write_invocation_metric,
    write_invocation_trace,
    write_routing_decision,
    write_routing_margin,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_records(f: Path) -> list[dict]:
    lines = [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


# ---------------------------------------------------------------------------
# write_invocation_metric
# ---------------------------------------------------------------------------

class TestWriteInvocationMetric:
    def test_creates_file_if_not_exists(self, tmp_path):
        mf = tmp_path / "metrics.jsonl"
        write_invocation_metric("agent-a", True, 1200.0, metrics_file=mf)
        assert mf.exists()

    def test_creates_parent_dirs(self, tmp_path):
        mf = tmp_path / "nested" / "dir" / "metrics.jsonl"
        write_invocation_metric("agent-a", True, 1200.0, metrics_file=mf)
        assert mf.exists()

    def test_record_type_is_invocation(self, tmp_path):
        mf = tmp_path / "m.jsonl"
        write_invocation_metric("agent-a", True, 500.0, metrics_file=mf)
        records = _read_records(mf)
        assert records[0]["record_type"] == "invocation"

    def test_success_flag_recorded(self, tmp_path):
        mf = tmp_path / "m.jsonl"
        write_invocation_metric("a", False, 200.0, failure_reason="timeout", metrics_file=mf)
        r = _read_records(mf)[0]
        assert r["success"] is False
        assert r["failure_reason"] == "timeout"

    def test_quality_score_optional(self, tmp_path):
        mf = tmp_path / "m.jsonl"
        write_invocation_metric("a", True, 300.0, metrics_file=mf)
        r = _read_records(mf)[0]
        assert r["quality_score"] is None

    def test_quality_score_stored(self, tmp_path):
        mf = tmp_path / "m.jsonl"
        write_invocation_metric("a", True, 300.0, quality_score=0.87, metrics_file=mf)
        r = _read_records(mf)[0]
        assert abs(r["quality_score"] - 0.87) < 1e-9

    def test_fallback_level_stored(self, tmp_path):
        mf = tmp_path / "m.jsonl"
        write_invocation_metric("a", True, 100.0, fallback_level=1, metrics_file=mf)
        r = _read_records(mf)[0]
        assert r["fallback_level"] == 1

    def test_cache_hit_stored(self, tmp_path):
        mf = tmp_path / "m.jsonl"
        write_invocation_metric("a", True, 10.0, cache_hit=True, metrics_file=mf)
        r = _read_records(mf)[0]
        assert r["cache_hit"] is True

    def test_timestamp_iso_utc(self, tmp_path):
        mf = tmp_path / "m.jsonl"
        write_invocation_metric("a", True, 10.0, metrics_file=mf)
        r = _read_records(mf)[0]
        ts = r["timestamp"]
        assert "T" in ts and "Z" in ts

    def test_multiple_records_appended(self, tmp_path):
        mf = tmp_path / "m.jsonl"
        for i in range(5):
            write_invocation_metric(f"agent-{i}", True, float(i * 100), metrics_file=mf)
        records = _read_records(mf)
        assert len(records) == 5
        assert records[2]["agent_name"] == "agent-2"

    def test_never_raises_on_permission_error(self, tmp_path):
        # Simulate unwritable path by using a directory as the target file
        mf = tmp_path  # directory, not a file — write will fail
        write_invocation_metric("a", True, 10.0, metrics_file=mf)  # must not raise


# ---------------------------------------------------------------------------
# write_routing_decision
# ---------------------------------------------------------------------------

class TestWriteRoutingDecision:
    def test_record_type_is_routing_decision(self, tmp_path):
        mf = tmp_path / "m.jsonl"
        write_routing_decision("agent-a", True, confidence=0.8, metrics_file=mf)
        r = _read_records(mf)[0]
        assert r["record_type"] == "routing_decision"

    def test_matched_agent_stored(self, tmp_path):
        mf = tmp_path / "m.jsonl"
        write_routing_decision("my-agent", False, metrics_file=mf)
        r = _read_records(mf)[0]
        assert r["matched_agent"] == "my-agent"

    def test_confidence_stored(self, tmp_path):
        mf = tmp_path / "m.jsonl"
        write_routing_decision("x", True, confidence=0.72, metrics_file=mf)
        r = _read_records(mf)[0]
        assert abs(r["confidence"] - 0.72) < 1e-9

    def test_matched_keywords_defaults_to_empty(self, tmp_path):
        mf = tmp_path / "m.jsonl"
        write_routing_decision("x", True, metrics_file=mf)
        r = _read_records(mf)[0]
        assert r["matched_keywords"] == []

    def test_matched_keywords_stored(self, tmp_path):
        mf = tmp_path / "m.jsonl"
        write_routing_decision("x", True, matched_keywords=["SDP", "canary"], metrics_file=mf)
        r = _read_records(mf)[0]
        assert r["matched_keywords"] == ["SDP", "canary"]

    def test_routing_ms_stored(self, tmp_path):
        mf = tmp_path / "m.jsonl"
        write_routing_decision("x", True, routing_ms=12.5, metrics_file=mf)
        r = _read_records(mf)[0]
        assert abs(r["routing_ms"] - 12.5) < 1e-9

    def test_dispatched_flag(self, tmp_path):
        mf = tmp_path / "m.jsonl"
        write_routing_decision("x", dispatched=False, metrics_file=mf)
        r = _read_records(mf)[0]
        assert r["dispatched"] is False


class TestWriteRoutingMargin:
    def test_routing_margin_fields_and_single_candidate(self, tmp_path):
        mf = tmp_path / "metrics.jsonl"
        write_routing_margin(
            top1_agent="alpha", top1_confidence=0.87, top2_agent="beta", top2_confidence=0.60,
            confidence_margin=0.27, routing_ms=12.0, metrics_file=mf,
        )
        write_routing_margin(
            top1_agent="solo-agent", top1_confidence=0.99, top2_agent=None, top2_confidence=0.0,
            confidence_margin=0.0, routing_ms=3.0, metrics_file=mf,
        )
        margins = [r for r in _read_records(mf) if r.get("record_type") == "routing_margin"]
        assert len(margins) == 2
        required = {"record_type", "timestamp", "top1_agent", "top1_confidence",
                    "top2_agent", "top2_confidence", "confidence_margin", "routing_ms"}
        assert not (required - set(margins[0]))
        assert abs(margins[0]["confidence_margin"] - 0.27) < 0.001
        assert margins[1]["confidence_margin"] == 0.0 and margins[1]["top2_agent"] is None


class TestWriteInvocationTrace:
    def test_invocation_trace_fields_and_iso_timestamp(self, tmp_path):
        import uuid
        from datetime import datetime

        tf = tmp_path / "ext-agent-trace.jsonl"
        inv_id = str(uuid.uuid4())
        write_invocation_trace(
            invocation_id=inv_id, agent_name="my-special-agent", query_hash="myhash99",
            routing_confidence=0.85, quality_score=0.75, latency_ms=123.4, trace_file=tf,
        )
        rec = _read_records(tf)[-1]
        assert rec["invocation_id"] == inv_id
        assert rec["agent_name"] == "my-special-agent"
        assert rec["query_hash"] == "myhash99"
        ts_str = rec.get("timestamp", "") or rec.get("ts", "")
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        assert ts.tzinfo is not None
