"""tests/unit/test_telemetry.py — Telemetry chain integrity, trace IDs, and query API tests."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from lib import telemetry as T


def _emit_to(event: str, path: Path, **kwargs) -> dict:
    """Emit one event to path and return the parsed record."""
    T.emit(event, _path=path, **kwargs)
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# Chain fork detection & audit review window
# ---------------------------------------------------------------------------

def test_chain_forks_before_the_reviewed_date_are_excused_and_later_ones_are_not(tmp_path):
    def entry(ts, prev):
        body = {"ts": ts, "event": "x", "prev_hash": prev}
        return {**body, "entry_hash": T._compute_entry_hash(body)}

    first = entry("2026-04-23T00:00:00+00:00", T._GENESIS_HASH)
    fork = entry("2026-04-24T00:00:00+00:00", "0" * 64)
    late = entry("2026-09-01T00:00:00+00:00", "1" * 64)
    log = tmp_path / "t.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in (first, fork)) + "\n", encoding="utf-8")
    assert T.chain_breaks(log) == ["2026-04-24T00:00:00+00:00"] and T.unreviewed_breaks(log) == []
    log.write_text("\n".join(json.dumps(e) for e in (first, fork, late)) + "\n", encoding="utf-8")
    assert T.unreviewed_breaks(log) == ["2026-09-01T00:00:00+00:00"]
    tampered = {**first, "event": "changed"}
    log.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"entry\ hash\ mismatch\ in\ telemetry\ log"):
        T.chain_breaks(log)


# ---------------------------------------------------------------------------
# Hash chain & integrity
# ---------------------------------------------------------------------------

def test_emit_adds_entry_hash(tmp_path):
    p = tmp_path / "tel.jsonl"
    record = _emit_to("test.event", p)
    assert "entry_hash" in record and "prev_hash" in record
    assert len(record["entry_hash"]) == 64 and len(record["prev_hash"]) == 64


def test_emit_chain_links(tmp_path):
    p = tmp_path / "tel.jsonl"
    r1 = _emit_to("test.one", p)
    r2 = _emit_to("test.two", p)
    assert r2["prev_hash"] == r1["entry_hash"]


def test_genesis_hash_on_first_entry(tmp_path, monkeypatch):
    p = tmp_path / "tel.jsonl"
    monkeypatch.setattr(T, "_prev_hash", T._GENESIS_HASH)
    record = _emit_to("test.genesis", p)
    assert record["prev_hash"] == T._GENESIS_HASH


def test_verify_integrity_valid_chain(tmp_path):
    p = tmp_path / "tel.jsonl"
    for event in ("a.one", "a.two", "a.three"):
        _emit_to(event, p)
    assert T.verify_integrity(p) is True


def test_verify_integrity_tampered_and_chain_break(tmp_path):
    p = tmp_path / "tel.jsonl"
    _emit_to("b.one", p)
    _emit_to("b.two", p)
    _emit_to("b.three", p)

    lines = p.read_text(encoding="utf-8").splitlines()
    second = json.loads(lines[1])
    tampered_event = list(lines)
    tampered_event[1] = json.dumps({**second, "event": "TAMPERED"})
    p.write_text("\n".join(tampered_event) + "\n", encoding="utf-8")
    assert T.verify_integrity(p) is False

    tampered_prev = list(lines)
    tampered_prev[1] = json.dumps({**second, "prev_hash": "0" * 64})
    p.write_text("\n".join(tampered_prev) + "\n", encoding="utf-8")
    assert T.verify_integrity(p) is False


def test_verify_integrity_empty_file_and_pre_hash_entries(tmp_path):
    assert T.verify_integrity(tmp_path / "nonexistent.jsonl") is True
    p = tmp_path / "tel.jsonl"
    old_entry = {"ts": "2026-01-01T00:00:00+00:00", "event": "legacy.event", "session_id": "old_001"}
    p.write_text(json.dumps(old_entry) + "\n", encoding="utf-8")
    _emit_to("new.event", p)
    assert T.verify_integrity(p) is True


def test_emit_backward_compatible(tmp_path):
    p = tmp_path / "tel.jsonl"
    record = _emit_to("compat.check", p, domain="finance", step="budget")
    assert record["event"] == "compat.check"
    assert record["domain"] == "finance" and record["step"] == "budget"
    assert "ts" in record and "session_id" in record


def test_init_prev_hash_load(tmp_path, monkeypatch):
    p = tmp_path / "tel_init.jsonl"
    monkeypatch.setattr(T, "_TELEMETRY_PATH", tmp_path / "nonexistent.jsonl")
    assert T._load_prev_hash() == T._GENESIS_HASH

    for i in range(3):
        _emit_to(f"init.{i}", p)
    last_record = json.loads([line for line in p.read_text(encoding="utf-8").splitlines() if line.strip()][-1])
    monkeypatch.setattr(T, "_TELEMETRY_PATH", p)
    assert T._load_prev_hash() == last_record["entry_hash"]


@pytest.mark.slow
def test_large_file_performance(tmp_path, capsys):
    p = tmp_path / "tel_perf.jsonl"
    start = time.perf_counter()
    for _ in range(1_000):
        T.emit("perf.event", _path=p)
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0, f"1K emits took {elapsed:.2f}s (limit: 5s)"
    assert len(p.read_text(encoding="utf-8").splitlines()) == 1_000
    assert T.verify_integrity(p) is True
    assert not capsys.readouterr().err


# ---------------------------------------------------------------------------
# Trace ID generation & query helpers
# ---------------------------------------------------------------------------

class TestGenerateTraceId:
    def test_returns_16_char_hex_and_matches_sha256(self):
        tid = T.generate_trace_id("sess_abc", "pipeline", 1_700_000_000_000)
        assert len(tid) == 16 and all(c in "0123456789abcdef" for c in tid)
        raw = f"sess_xyz:pipeline:{(1_700_000_005_000 // 10) * 10}"
        assert T.generate_trace_id("sess_xyz", "pipeline", 1_700_000_005_000, 10) == hashlib.sha256(raw.encode()).hexdigest()[:16]
        assert len(T.generate_trace_id("", "", 0)) == 16

    def test_quantization_and_identity_discrimination(self):
        assert T.generate_trace_id("sess_abc", "pipeline", 1_700_000_000_000) == T.generate_trace_id("sess_abc", "pipeline", 1_700_000_000_009)
        assert T.generate_trace_id("sess_abc", "pipeline", 1_700_000_000_009) != T.generate_trace_id("sess_abc", "pipeline", 1_700_000_000_010)
        assert T.generate_trace_id("sess_aaa", "pipeline", 1_700_000_000_000) != T.generate_trace_id("sess_bbb", "pipeline", 1_700_000_000_000)
        assert T.generate_trace_id("sess_abc", "pipeline", 1_700_000_000_000) != T.generate_trace_id("sess_abc", "work_loop", 1_700_000_000_000)
        assert T.generate_trace_id("sess_abc", "agent", 1_700_000_000_999, quantization_ms=1000) == T.generate_trace_id("sess_abc", "agent", 1_700_000_000_000, quantization_ms=1000)
        assert T.generate_trace_id("sess_abc", "agent", 1_700_000_000_999, quantization_ms=1000) != T.generate_trace_id("sess_abc", "agent", 1_700_000_001_000, quantization_ms=1000)


class TestQueryEvents:
    def _make_jsonl(self, tmp_path: Path, entries: list[dict]) -> Path:
        p = tmp_path / "telemetry.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
        return p

    def test_query_by_trace_handles_missing_malformed_and_matches(self, tmp_path):
        assert T.query_events_by_trace("abc123", path=tmp_path / "missing.jsonl") == []
        p = self._make_jsonl(tmp_path, [{"event": "test", "trace_id": "aaaa"}, {"event": "has_trace", "trace_id": "xyz"}])
        assert T.query_events_by_trace("bbbb", path=p) == []
        assert [r["event"] for r in T.query_events_by_trace("xyz", path=p)] == ["has_trace"]
        malformed = tmp_path / "bad.jsonl"
        malformed.write_text('{"trace_id":"ok","event":"g1"}\nnot-json\n{"trace_id":"ok","event":"g2"}\n', encoding="utf-8")
        assert len(T.query_events_by_trace("ok", path=malformed)) == 2

    def test_query_by_session_handles_missing_and_matches(self, tmp_path):
        assert T.query_events_by_session("sess_x", path=tmp_path / "missing.jsonl") == []
        p = self._make_jsonl(tmp_path, [
            {"event": "no_session"},
            {"session_id": "sess_a", "event": "x"},
            {"session_id": "sess_b", "event": "y"},
            {"session_id": "sess_a", "event": "z"},
        ])
        assert T.query_events_by_session("sess_z", path=p) == []
        res = T.query_events_by_session("sess_a", path=p)
        assert [r["event"] for r in res] == ["x", "z"]
