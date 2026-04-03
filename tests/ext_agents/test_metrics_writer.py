"""tests/ext_agents/test_metrics_writer.py — EA-10a metrics JSONL writer tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.metrics_writer import write_invocation_metric, write_routing_decision  # type: ignore


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
