"""
Tests for _program_health_oneliner() in work/briefing.py.

Verifies that the function correctly parses xpf-program-structure.md
and emits the expected one-liner format.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from work.briefing import _program_health_oneliner

# ---------------------------------------------------------------------------
# Fixture: minimal xpf-program-structure.md body
# ---------------------------------------------------------------------------

_XPF_BODY_MEDIUM = """\
---
title: XPF Program Structure
---

## Signal Summary

| Signal | Count | % |
|--------|-------|---|
| 🔴 Red | 7 | 18% |
| 🟡 Yellow | 24 | 63% |
| 🟢 Green | 7 | 18% |

Overall Program Risk Posture: 🟡 MEDIUM
"""

_XPF_BODY_HIGH = """\
---
title: XPF Program Structure
---

## Signal Summary

| Signal | Count | % |
|--------|-------|---|
| 🔴 Red | 15 | 40% |
| 🟡 Yellow | 10 | 27% |
| 🟢 Green | 12 | 32% |

Overall Program Risk Posture: 🔴 HIGH
"""

_XPF_BODY_NO_SIGNALS = """\
---
title: XPF Program Structure
---

## Overview
This document has no signal summary table.

Overall Program Risk Posture: 🟢 LOW
"""


class TestProgramHealthOneliner:
    """Test _program_health_oneliner with filesystem fixtures."""

    @pytest.fixture(autouse=True)
    def work_dir(self, tmp_path, monkeypatch):
        import work.briefing as mod
        monkeypatch.setattr(mod, "_WORK_STATE_DIR", tmp_path)
        self._state_dir = tmp_path

    def _write_xpf(self, content):
        (self._state_dir / "xpf-program-structure.md").write_text(
            content, encoding="utf-8"
        )

    def test_medium_risk(self):
        self._write_xpf(_XPF_BODY_MEDIUM)
        result = _program_health_oneliner()
        assert "MEDIUM" in result
        assert "R:7" in result
        assert "Y:24" in result
        assert "G:7" in result

    def test_high_risk(self):
        self._write_xpf(_XPF_BODY_HIGH)
        result = _program_health_oneliner()
        assert "HIGH" in result
        assert "R:15" in result
        assert "G:12" in result

    def test_missing_file_returns_empty(self):
        result = _program_health_oneliner()
        assert result == ""

    def test_no_signal_counts(self):
        self._write_xpf(_XPF_BODY_NO_SIGNALS)
        result = _program_health_oneliner()
        assert "LOW" in result
        # Counts default to 0
        assert "R:0" in result

    def test_output_format(self):
        self._write_xpf(_XPF_BODY_MEDIUM)
        result = _program_health_oneliner()
        assert result.startswith("Program health:")
        assert "(" in result and ")" in result
