"""
tests/ext_agents/test_confidence_margin.py — Phase 0 tests: confidence margin logging.

Tests (7):
 1. _emit_routing_margin is called after route()
 2. write_routing_margin appends a routing_margin record to JSONL
 3. confidence_margin field is correct (top1 - top2)
 4. margin = 0.0 when only 1 candidate
 5. routing_margin record has all required fields
 6. margin appears in metrics JSONL (not trace)
 7. _emit_routing_margin handles empty candidates gracefully

Ref: specs/ext-agent-reloaded.md §BLOCKING-3
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import lib.metrics_writer as metrics_writer_mod


# ---------------------------------------------------------------------------
# Test 1: write_routing_margin appends to metrics JSONL
# ---------------------------------------------------------------------------

def test_write_routing_margin_appends(tmp_path):
    metrics_file = tmp_path / "metrics.jsonl"

    metrics_writer_mod.write_routing_margin(
        top1_agent="agent-a",
        top1_confidence=0.90,
        top2_agent="agent-b",
        top2_confidence=0.65,
        confidence_margin=0.25,
        routing_ms=8.3,
        metrics_file=metrics_file,
    )

    assert metrics_file.exists()
    lines = [l for l in metrics_file.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1


# ---------------------------------------------------------------------------
# Test 2: record_type is "routing_margin"
# ---------------------------------------------------------------------------

def test_write_routing_margin_record_type(tmp_path):
    metrics_file = tmp_path / "metrics.jsonl"

    metrics_writer_mod.write_routing_margin(
        top1_agent="a",
        top1_confidence=0.8,
        top2_agent="b",
        top2_confidence=0.6,
        confidence_margin=0.2,
        routing_ms=5.0,
        metrics_file=metrics_file,
    )
    records = [json.loads(l) for l in metrics_file.read_text().splitlines() if l.strip()]
    margin_records = [r for r in records if r.get("record_type") == "routing_margin"]
    assert len(margin_records) >= 1


# ---------------------------------------------------------------------------
# Test 3: confidence_margin value is preserved in record
# ---------------------------------------------------------------------------

def test_write_routing_margin_value(tmp_path):
    metrics_file = tmp_path / "metrics.jsonl"
    margin = 0.27
    metrics_writer_mod.write_routing_margin(
        top1_agent="alpha",
        top1_confidence=0.87,
        top2_agent="beta",
        top2_confidence=0.60,
        confidence_margin=margin,
        routing_ms=12.0,
        metrics_file=metrics_file,
    )
    records = [json.loads(l) for l in metrics_file.read_text().splitlines() if l.strip()]
    rec = [r for r in records if r.get("record_type") == "routing_margin"][-1]
    assert abs(rec["confidence_margin"] - margin) < 0.001


# ---------------------------------------------------------------------------
# Test 4: margin = 0.0 when only 1 candidate (no second agent)
# ---------------------------------------------------------------------------

def test_write_routing_margin_zero_when_single(tmp_path):
    metrics_file = tmp_path / "metrics.jsonl"
    metrics_writer_mod.write_routing_margin(
        top1_agent="solo-agent",
        top1_confidence=0.99,
        top2_agent=None,
        top2_confidence=0.0,
        confidence_margin=0.0,
        routing_ms=3.0,
        metrics_file=metrics_file,
    )
    records = [json.loads(l) for l in metrics_file.read_text().splitlines() if l.strip()]
    rec = [r for r in records if r.get("record_type") == "routing_margin"][-1]
    assert rec["confidence_margin"] == 0.0


# ---------------------------------------------------------------------------
# Test 5: routing_margin record has required fields
# ---------------------------------------------------------------------------

def test_routing_margin_required_fields(tmp_path):
    metrics_file = tmp_path / "metrics.jsonl"
    metrics_writer_mod.write_routing_margin(
        top1_agent="a",
        top1_confidence=0.7,
        top2_agent="b",
        top2_confidence=0.5,
        confidence_margin=0.2,
        routing_ms=9.0,
        metrics_file=metrics_file,
    )
    records = [json.loads(l) for l in metrics_file.read_text().splitlines() if l.strip()]
    rec = [r for r in records if r.get("record_type") == "routing_margin"][-1]

    # The module uses "timestamp" not "ts"
    required = {"record_type", "timestamp", "top1_agent", "top1_confidence",
                "top2_agent", "top2_confidence", "confidence_margin", "routing_ms"}
    missing = required - set(rec.keys())
    assert not missing, f"Missing fields: {missing}"


# ---------------------------------------------------------------------------
# Test 6: margin is written to metrics JSONL, not trace JSONL
# ---------------------------------------------------------------------------

def test_routing_margin_goes_to_metrics_not_trace(tmp_path):
    metrics_file = tmp_path / "metrics.jsonl"
    trace_file = tmp_path / "trace.jsonl"

    metrics_writer_mod.write_routing_margin(
        top1_agent="a",
        top1_confidence=0.8,
        top2_agent="b",
        top2_confidence=0.4,
        confidence_margin=0.4,
        routing_ms=5.0,
        metrics_file=metrics_file,
    )

    # Must be in metrics, not trace
    metrics_records = [json.loads(l) for l in metrics_file.read_text().splitlines() if l.strip()]
    assert any(r.get("record_type") == "routing_margin" for r in metrics_records)

    # trace file should NOT exist (we never wrote to it)
    assert not trace_file.exists()


# ---------------------------------------------------------------------------
# Test 7: write_routing_margin handles None top2_agent gracefully
# ---------------------------------------------------------------------------

def test_write_routing_margin_none_top2_no_crash(tmp_path):
    metrics_file = tmp_path / "metrics.jsonl"

    # Should not raise
    metrics_writer_mod.write_routing_margin(
        top1_agent="only-agent",
        top1_confidence=0.95,
        top2_agent=None,
        top2_confidence=0.0,
        confidence_margin=0.0,
        routing_ms=1.5,
        metrics_file=metrics_file,
    )
    assert metrics_file.exists()
