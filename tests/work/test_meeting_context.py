"""
Tests for _xpf_meeting_context() and _XPF_KEYWORDS in work/meetings.py.

Validates keyword matching, program context extraction, and edge cases.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from work.meetings import _xpf_meeting_context, _XPF_KEYWORDS

# ---------------------------------------------------------------------------
# Fixture: xpf-program-structure.md with workstream metrics
# ---------------------------------------------------------------------------

_XPF_BODY = """\
---
title: XPF Program Structure
---

## Signal Summary

Overall Program Risk Posture: 🟡 MEDIUM

### WS1 — Fleet Convergence

| ID | Metric Name | Current Value | Target | Signal | Source |
|----|-------------|---------------|--------|--------|--------|
| M01 | Fleet Size | **64 clusters** | 75 | 🟢 | Kusto |
| M05 | Deploy P90 | **14 hrs** | 12 hrs | 🔴 | Kusto |

### WS3 — Direct Drive Migration

| ID | Metric Name | Current Value | Target | Signal | Source |
|----|-------------|---------------|--------|--------|--------|
| M10 | Migration % | **42%** | 80% | 🔴 | Manual |
"""


class TestXpfKeywords:
    def test_keywords_are_lowercase(self):
        for kw in _XPF_KEYWORDS:
            assert kw == kw.lower(), f"Keyword {kw!r} should be lowercase"

    def test_core_keywords_present(self):
        assert "xpf" in _XPF_KEYWORDS
        assert "platform fleet" in _XPF_KEYWORDS
        assert "deployment velocity" in _XPF_KEYWORDS


class TestXpfMeetingContext:
    @pytest.fixture(autouse=True)
    def work_dir(self, tmp_path, monkeypatch):
        import work.meetings as mod
        monkeypatch.setattr(mod, "_WORK_STATE_DIR", tmp_path)
        self._state_dir = tmp_path

    def _write_xpf(self, content=_XPF_BODY):
        (self._state_dir / "xpf-program-structure.md").write_text(
            content, encoding="utf-8"
        )

    def test_matching_title(self):
        self._write_xpf()
        result = _xpf_meeting_context("XPF Weekly Standup")
        assert len(result) > 0
        assert any("Program Risk" in line or "XPF" in line for line in result)

    def test_case_insensitive(self):
        self._write_xpf()
        result = _xpf_meeting_context("xpf weekly standup")
        assert len(result) > 0

    def test_non_matching_title(self):
        self._write_xpf()
        result = _xpf_meeting_context("Budget Review Q2")
        assert result == []

    def test_missing_file(self):
        result = _xpf_meeting_context("XPF Weekly")
        assert result == []

    def test_platform_fleet_keyword(self):
        self._write_xpf()
        result = _xpf_meeting_context("Platform Fleet Status Review")
        assert len(result) > 0

    def test_deployment_velocity_keyword(self):
        self._write_xpf()
        result = _xpf_meeting_context("Deployment Velocity Deep Dive")
        assert len(result) > 0

    def test_max_six_lines(self):
        self._write_xpf()
        result = _xpf_meeting_context("XPF Fleet Convergence Review")
        assert len(result) <= 6

    def test_includes_risk_posture(self):
        self._write_xpf()
        result = _xpf_meeting_context("XPF Monthly LT")
        risk_lines = [l for l in result if "Risk" in l]
        assert len(risk_lines) >= 1

    def test_direct_drive_keyword(self):
        self._write_xpf()
        result = _xpf_meeting_context("Direct Drive Migration Status")
        assert len(result) > 0

    def test_empty_body(self):
        (self._state_dir / "xpf-program-structure.md").write_text(
            "---\ntitle: empty\n---\n", encoding="utf-8"
        )
        result = _xpf_meeting_context("XPF Review")
        # Should not crash, may return empty or just risk line
        assert isinstance(result, list)
