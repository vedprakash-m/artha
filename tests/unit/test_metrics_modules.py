"""Direct unit tests for lib.metrics_analysis and lib.metrics_collection."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from lib import metrics_analysis as ma
from lib import metrics_collection as mc

ARTHA_TEST_OWNER = {
    "requirements": ["FR-29", "FR-30", "RA-10"],
    "modules": ["scripts/lib/metrics_analysis.py", "scripts/lib/metrics_collection.py"],
}


def test_metrics_analysis_score_rank_and_partition(tmp_path):
    high = {"urgency": 5, "impact": 5, "age_days": 0, "id": "hi"}
    mid = {"urgency": 3, "impact": 3, "age_days": 5, "id": "mid"}
    noise = {"urgency": 1, "impact": 1, "age_days": 20, "id": "low"}

    assert ma.score_signal(high) == pytest.approx(1.0)
    assert ma.score_signal(noise) < 0.2
    ranked = ma.rank_signals([noise, mid, high], artha_dir=tmp_path)
    assert [s["id"] for s in ranked] == ["hi", "mid", "low"]

    promoted, standard, suppressed = ma.partition_signals([noise, mid, high], artha_dir=tmp_path)
    assert [s["id"] for s in promoted] == ["hi"]
    assert [s["id"] for s in standard] == ["mid"]
    assert [s["id"] for s in suppressed] == ["low"]


def test_metrics_analysis_compute_digest_and_render(tmp_path, monkeypatch):
    metrics_file = tmp_path / "ext-agent-metrics.jsonl"
    now_iso = datetime.now(timezone.utc).isoformat()
    metrics_file.write_text(
        "\n".join([
            json.dumps({"timestamp": now_iso, "agent_name": "a1", "quality_score": 0.9, "latency_ms": 100.0, "success": True}),
            json.dumps({"timestamp": now_iso, "agent_name": "a1", "quality_score": 0.5, "latency_ms": 200.0, "success": False}),
            json.dumps({"timestamp": now_iso, "record_type": "routing_margin", "confidence_margin": 0.45}),
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ma, "_METRICS_FILE", metrics_file)

    digest = ma._compute_digest(weeks=1)
    assert digest["fleet"]["total_invocations"] == 2
    assert digest["fleet"]["total_errors"] == 1
    assert digest["routing"]["confidence_margin_distribution"]["0.3-0.6"] == 1
    assert digest["agents"]["a1"]["avg_quality"] == pytest.approx(0.7)
    md = ma._render_markdown(digest)
    assert "a1" in md


def test_catchup_metrics_collection_lifecycle_and_cap(tmp_path):
    m = mc.CatchUpMetrics(artha_dir=tmp_path)
    with m.phase("preflight"):
        m.record("emails_processed", 12)
        m.increment("emails_processed", 3)
        m.record_eval("accuracy", 0.95)
    with m.step("fetch.gmail"):
        m.record_step("fetch.calendar", 0.12)

    with pytest.raises(RuntimeError, match="boom"):
        with m.phase("broken"):
            raise RuntimeError("boom")

    snap = m.snapshot()
    assert snap["counters"]["emails_processed"] == 15
    assert snap["phases"]["broken"]["error"] == "boom"
    assert "total_seconds:" in m.health_check_summary()
    assert "preflight" in m.timing_report()

    for _ in range(35):
        out = m.save()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert len(saved) == 30
