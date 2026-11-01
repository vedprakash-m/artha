"""tests/unit/test_routing_audit.py — DEBT-020: routing_quality metric emission."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))

from lib.metrics_writer import write_routing_margin  # type: ignore[import]


class TestWriteRoutingMarginIncludesQuality:
    """write_routing_margin records include routing_quality.keyword_miss_rate (DEBT-020)."""

    def test_record_has_routing_quality_field(self, tmp_path):
        mf = tmp_path / "metrics.jsonl"
        write_routing_margin(
            top1_agent="agent_a",
            top1_confidence=0.80,
            top2_agent="agent_b",
            top2_confidence=0.60,
            confidence_margin=0.20,
            routing_ms=5.0,
            keyword_miss_rate=0.12,
            metrics_file=mf,
        )
        record = json.loads(mf.read_text())
        assert "routing_quality" in record
        assert "keyword_miss_rate" in record["routing_quality"]
        assert record["routing_quality"]["keyword_miss_rate"] == pytest.approx(0.12)

    def test_record_without_miss_rate(self, tmp_path):
        mf = tmp_path / "metrics.jsonl"
        write_routing_margin(
            top1_agent="agent_a",
            top1_confidence=0.80,
            top2_agent=None,
            top2_confidence=0.0,
            confidence_margin=0.80,
            routing_ms=3.0,
            metrics_file=mf,
        )
        record = json.loads(mf.read_text())
        assert "routing_quality" in record
        assert record["routing_quality"]["keyword_miss_rate"] is None


class TestAnalyzeRoutingAuditKeywordMissRate:
    """analyze_routing_audit computes rolling keyword_miss_rate (DEBT-020)."""

    def _now_str(self, offset_days: int = 0) -> str:
        dt = datetime.now(timezone.utc) - timedelta(days=offset_days)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _write_margin_records(self, tmp_file: Path, miss_rates: list[float]) -> None:
        for mr in miss_rates:
            record = {
                "timestamp": self._now_str(0),
                "record_type": "routing_margin",
                "top1_agent": "agent_a",
                "top1_confidence": 0.80,
                "top2_agent": None,
                "top2_confidence": 0.0,
                "confidence_margin": 0.80,
                "routing_ms": 1.0,
                "routing_quality": {"keyword_miss_rate": mr},
            }
            with tmp_file.open("a") as f:
                f.write(json.dumps(record) + "\n")

    def test_keyword_miss_rate_computed(self, tmp_path, monkeypatch):
        mf = tmp_path / "metrics.jsonl"
        self._write_margin_records(mf, [0.05, 0.10, 0.15])

        import eval_runner  # type: ignore[import]
        monkeypatch.setattr(eval_runner, "_EXT_AGENT_METRICS", mf)

        data = eval_runner.analyze_routing_audit(days=7)
        assert data["keyword_miss_rate"] == pytest.approx(0.10)

    def test_no_records_returns_none_miss_rate(self, tmp_path, monkeypatch):
        mf = tmp_path / "metrics.jsonl"  # empty

        import eval_runner  # type: ignore[import]
        monkeypatch.setattr(eval_runner, "_EXT_AGENT_METRICS", mf)

        data = eval_runner.analyze_routing_audit(days=7)
        assert data.get("keyword_miss_rate") is None

    def test_alert_emitted_when_above_threshold(self, tmp_path, monkeypatch):
        mf = tmp_path / "metrics.jsonl"
        # 20% average miss rate — above 10% threshold
        self._write_margin_records(mf, [0.20, 0.20, 0.20])

        import eval_runner  # type: ignore[import]
        monkeypatch.setattr(eval_runner, "_EXT_AGENT_METRICS", mf)

        data = eval_runner.analyze_routing_audit(days=7)
        assert data["keyword_miss_rate_alert"] is not None
        assert "ALERT" in data["keyword_miss_rate_alert"]
        assert "keyword_miss_rate" in data["keyword_miss_rate_alert"]

    def test_no_alert_below_threshold(self, tmp_path, monkeypatch):
        mf = tmp_path / "metrics.jsonl"
        self._write_margin_records(mf, [0.02, 0.03, 0.04])

        import eval_runner  # type: ignore[import]
        monkeypatch.setattr(eval_runner, "_EXT_AGENT_METRICS", mf)

        data = eval_runner.analyze_routing_audit(days=7)
        assert data.get("keyword_miss_rate_alert") is None
