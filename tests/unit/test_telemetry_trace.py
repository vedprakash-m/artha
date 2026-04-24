"""
tests/unit/test_telemetry_trace.py — Tests for ST-06: generate_trace_id + query functions.
specs/steal.md §15.2.6
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"))

import telemetry as T


# ---------------------------------------------------------------------------
# generate_trace_id
# ---------------------------------------------------------------------------


class TestGenerateTraceId:
    def test_returns_16_char_hex_string(self):
        tid = T.generate_trace_id("sess_abc", "pipeline", 1_700_000_000_000)
        assert isinstance(tid, str)
        assert len(tid) == 16
        assert all(c in "0123456789abcdef" for c in tid)

    def test_deterministic_same_inputs(self):
        t1 = T.generate_trace_id("sess_abc", "pipeline", 1_700_000_000_000)
        t2 = T.generate_trace_id("sess_abc", "pipeline", 1_700_000_000_000)
        assert t1 == t2

    def test_same_within_quantization_window(self):
        # Both timestamps fall within the same 10ms bucket
        t1 = T.generate_trace_id("sess_abc", "pipeline", 1_700_000_000_000)
        t2 = T.generate_trace_id("sess_abc", "pipeline", 1_700_000_000_009)
        assert t1 == t2

    def test_different_across_quantization_boundary(self):
        t1 = T.generate_trace_id("sess_abc", "pipeline", 1_700_000_000_009)
        t2 = T.generate_trace_id("sess_abc", "pipeline", 1_700_000_000_010)
        assert t1 != t2

    def test_different_session_id_produces_different_trace(self):
        t1 = T.generate_trace_id("sess_aaa", "pipeline", 1_700_000_000_000)
        t2 = T.generate_trace_id("sess_bbb", "pipeline", 1_700_000_000_000)
        assert t1 != t2

    def test_different_agent_name_produces_different_trace(self):
        t1 = T.generate_trace_id("sess_abc", "pipeline", 1_700_000_000_000)
        t2 = T.generate_trace_id("sess_abc", "work_loop", 1_700_000_000_000)
        assert t1 != t2

    def test_custom_quantization_ms(self):
        # With 1000ms quantization, timestamps within same 1s bucket match
        t1 = T.generate_trace_id("sess_abc", "agent", 1_700_000_000_999, quantization_ms=1000)
        t2 = T.generate_trace_id("sess_abc", "agent", 1_700_000_000_000, quantization_ms=1000)
        assert t1 == t2

    def test_custom_quantization_ms_across_boundary(self):
        t1 = T.generate_trace_id("sess_abc", "agent", 1_700_000_000_999, quantization_ms=1000)
        t2 = T.generate_trace_id("sess_abc", "agent", 1_700_000_001_000, quantization_ms=1000)
        assert t1 != t2

    def test_matches_manual_sha256(self):
        session_id = "sess_xyz"
        agent_name = "pipeline"
        timestamp_ms = 1_700_000_005_000
        quantization_ms = 10
        quantized_ts = (timestamp_ms // quantization_ms) * quantization_ms
        raw = f"{session_id}:{agent_name}:{quantized_ts}"
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        result = T.generate_trace_id(session_id, agent_name, timestamp_ms, quantization_ms)
        assert result == expected

    def test_empty_strings_produce_stable_output(self):
        tid = T.generate_trace_id("", "", 0)
        assert len(tid) == 16


# ---------------------------------------------------------------------------
# query_events_by_trace
# ---------------------------------------------------------------------------


class TestQueryEventsByTrace:
    def _make_jsonl(self, tmp_path: Path, entries: list[dict]) -> Path:
        p = tmp_path / "telemetry.jsonl"
        lines = [json.dumps(e) for e in entries]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def test_returns_empty_when_file_absent(self, tmp_path):
        result = T.query_events_by_trace("abc123", path=tmp_path / "missing.jsonl")
        assert result == []

    def test_returns_empty_on_no_match(self, tmp_path):
        p = self._make_jsonl(tmp_path, [{"event": "test", "trace_id": "aaaa"}])
        result = T.query_events_by_trace("bbbb", path=p)
        assert result == []

    def test_returns_matching_entries(self, tmp_path):
        p = self._make_jsonl(tmp_path, [
            {"event": "a", "trace_id": "match001"},
            {"event": "b", "trace_id": "other"},
            {"event": "c", "trace_id": "match001"},
        ])
        result = T.query_events_by_trace("match001", path=p)
        assert len(result) == 2
        assert all(r["trace_id"] == "match001" for r in result)
        assert result[0]["event"] == "a"
        assert result[1]["event"] == "c"

    def test_entries_without_trace_id_not_returned(self, tmp_path):
        p = self._make_jsonl(tmp_path, [
            {"event": "no_trace"},
            {"event": "has_trace", "trace_id": "xyz"},
        ])
        result = T.query_events_by_trace("xyz", path=p)
        assert len(result) == 1
        assert result[0]["event"] == "has_trace"

    def test_handles_malformed_lines_gracefully(self, tmp_path):
        p = tmp_path / "telemetry.jsonl"
        p.write_text(
            '{"trace_id": "ok", "event": "good"}\nnot-json\n{"trace_id": "ok", "event": "good2"}\n',
            encoding="utf-8",
        )
        result = T.query_events_by_trace("ok", path=p)
        assert len(result) == 2

    def test_empty_file_returns_empty_list(self, tmp_path):
        p = tmp_path / "telemetry.jsonl"
        p.write_text("", encoding="utf-8")
        result = T.query_events_by_trace("any", path=p)
        assert result == []


# ---------------------------------------------------------------------------
# query_events_by_session
# ---------------------------------------------------------------------------


class TestQueryEventsBySession:
    def _make_jsonl(self, tmp_path: Path, entries: list[dict]) -> Path:
        p = tmp_path / "telemetry.jsonl"
        lines = [json.dumps(e) for e in entries]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def test_returns_empty_when_file_absent(self, tmp_path):
        result = T.query_events_by_session("sess_x", path=tmp_path / "missing.jsonl")
        assert result == []

    def test_returns_matching_entries(self, tmp_path):
        p = self._make_jsonl(tmp_path, [
            {"session_id": "sess_a", "event": "x"},
            {"session_id": "sess_b", "event": "y"},
            {"session_id": "sess_a", "event": "z"},
        ])
        result = T.query_events_by_session("sess_a", path=p)
        assert len(result) == 2
        assert all(r["session_id"] == "sess_a" for r in result)

    def test_returns_empty_no_match(self, tmp_path):
        p = self._make_jsonl(tmp_path, [{"session_id": "sess_a", "event": "x"}])
        result = T.query_events_by_session("sess_z", path=p)
        assert result == []

    def test_handles_entries_without_session_id(self, tmp_path):
        p = self._make_jsonl(tmp_path, [
            {"event": "no_session"},
            {"session_id": "sess_a", "event": "with_session"},
        ])
        result = T.query_events_by_session("sess_a", path=p)
        assert len(result) == 1
        assert result[0]["event"] == "with_session"
