"""
Tests for _load_program_metrics() in narrative/_base.py.

Validates parsing of xpf-program-structure.md signal summary, risk posture,
workstream data, and key_metrics red-metric extraction.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from narrative_engine import NarrativeEngine

# ---------------------------------------------------------------------------
# Minimal xpf-program-structure.md fixture
# ---------------------------------------------------------------------------

_XPF_BODY = """\
## Signal Summary

| Signal | Count | % | Trend |
|--------|-------|---|-------|
| 🔴 Red | 7 | 18% | — |
| 🟡 Yellow | 24 | 63% | — |
| 🟢 Green | 7 | 18% | — |

Overall Program Risk Posture: 🟡 MEDIUM
Rationale: Fleet deployment latency exceeds SLA; XStore migration blocked.

### WS1 — Fleet Convergence

**LT Surface:** Monthly LT review

| ID | Metric Name | Current Value | Target | Signal | Source |
|----|-------------|---------------|--------|--------|--------|
| M01 | Fleet Size | **64 clusters** | 75 | 🟢 | Kusto |
| M04 | Deploy P50 | **4 hrs** | 4 hrs | 🟢 | Kusto |
| M05 | Deploy P90 | **14 hrs** | 12 hrs | 🔴 | Kusto |

### WS2 — XStore Direct Drive Migration

**LT Surface:** Quarterly review

| ID | Metric Name | Current Value | Target | Signal | Source |
|----|-------------|---------------|--------|--------|--------|
| M10 | Migration % | **42%** | 80% | 🔴 | Manual |
| M11 | Cluster Coverage | **12/64** | 64/64 | 🟡 | Manual |
"""


def _make_engine(tmp_path, xpf_body=_XPF_BODY, extras=None):
    """Create a NarrativeEngine with a temp state dir containing xpf-program-structure.md."""
    state_dir = tmp_path / "state" / "work"
    state_dir.mkdir(parents=True)

    xpf = state_dir / "xpf-program-structure.md"
    xpf.write_text(f"---\ntitle: XPF\n---\n{xpf_body}", encoding="utf-8")

    # Minimal required files
    for name in ["work-projects", "work-calendar", "work-comms", "work-career"]:
        f = state_dir / f"{name}.md"
        if not f.exists():
            f.write_text(f"---\ntitle: {name}\n---\nNo data.\n", encoding="utf-8")

    if extras:
        for name, content in extras.items():
            (state_dir / name).write_text(content, encoding="utf-8")

    engine = NarrativeEngine.__new__(NarrativeEngine)
    engine._state_dir = state_dir
    engine._cache = {}

    # Wire up _body to read from our temp dir
    def _body(stem):
        p = state_dir / f"{stem}.md"
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8")
        # Strip frontmatter
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                return text[end + 3:].strip()
        return text
    engine._body = _body

    return engine


class TestLoadProgramMetrics:
    def test_signal_summary(self, tmp_path):
        engine = _make_engine(tmp_path)
        m = engine._load_program_metrics()
        assert m["signal_summary"]["red"] == 7
        assert m["signal_summary"]["yellow"] == 24
        assert m["signal_summary"]["green"] == 7

    def test_risk_posture(self, tmp_path):
        engine = _make_engine(tmp_path)
        m = engine._load_program_metrics()
        assert m["risk_posture"] == "MEDIUM"

    def test_risk_rationale(self, tmp_path):
        engine = _make_engine(tmp_path)
        m = engine._load_program_metrics()
        assert "deployment latency" in m["risk_rationale"].lower()

    def test_workstreams_parsed(self, tmp_path):
        engine = _make_engine(tmp_path)
        m = engine._load_program_metrics()
        assert len(m["workstreams"]) == 2
        ws1 = m["workstreams"][0]
        assert ws1["id"] == "WS1"
        assert "Fleet Convergence" in ws1["name"]
        assert ws1["lt_surface"] == "Monthly LT review"

    def test_workstream_signals(self, tmp_path):
        engine = _make_engine(tmp_path)
        m = engine._load_program_metrics()
        ws1 = m["workstreams"][0]
        assert ws1["signals"]["red"] == 1  # M05
        assert ws1["signals"]["green"] == 2  # M01, M04

    def test_key_metrics_red_only(self, tmp_path):
        engine = _make_engine(tmp_path)
        m = engine._load_program_metrics()
        # Only red metrics appear in key_metrics
        for km in m["key_metrics"]:
            assert km["signal"] == "🔴"
        ids = {km["id"] for km in m["key_metrics"]}
        assert "M05" in ids
        assert "M10" in ids

    def test_top_metric_prefers_red(self, tmp_path):
        engine = _make_engine(tmp_path)
        m = engine._load_program_metrics()
        ws2 = m["workstreams"][1]
        assert "🔴" in ws2["top_metric"]

    def test_caching(self, tmp_path):
        engine = _make_engine(tmp_path)
        m1 = engine._load_program_metrics()
        m2 = engine._load_program_metrics()
        assert m1 is m2  # Same dict object from cache

    def test_missing_xpf_file(self, tmp_path):
        engine = _make_engine(tmp_path, xpf_body="")
        # Override _body to return empty
        engine._body = lambda stem: ""
        m = engine._load_program_metrics()
        assert m["risk_posture"] == ""
        assert m["workstreams"] == []

    def test_empty_body_returns_defaults(self, tmp_path):
        engine = _make_engine(tmp_path, xpf_body="Nothing here.")
        m = engine._load_program_metrics()
        assert m["signal_summary"]["red"] == 0
        assert m["workstreams"] == []
