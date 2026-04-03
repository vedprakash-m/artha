"""tests/ext_agents/test_ext_agent_audit.py — EA-11a audit event writer tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.ext_agent_audit import write_ext_agent_event  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_rows(audit_file: Path) -> list[str]:
    return [ln for ln in audit_file.read_text(encoding="utf-8").splitlines() if ln.startswith("|")]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWriteExtAgentEvent:
    def test_appends_row_to_existing_file(self, tmp_path):
        audit = tmp_path / "work-audit.md"
        audit.write_text(
            "| Timestamp | Connector | Failure Mode | Signal | Detail |\n"
            "| --- | --- | --- | --- | --- |\n",
            encoding="utf-8",
        )
        write_ext_agent_event("EXT_AGENT_ROUTED", "my-agent", "confidence=0.75", audit_file=audit)
        rows = _read_rows(audit)
        assert len(rows) == 3  # header + separator + new row
        new_row = rows[-1]
        assert "my-agent" in new_row
        assert "EXT_AGENT_ROUTED" in new_row
        assert "confidence=0.75" in new_row
        assert "AR-9" in new_row

    def test_row_contains_iso_timestamp(self, tmp_path):
        audit = tmp_path / "work-audit.md"
        audit.write_text("| h |\n", encoding="utf-8")
        write_ext_agent_event("EXT_AGENT_INVOKED", "a", "ok", audit_file=audit)
        rows = _read_rows(audit)
        # Timestamp column is second pipe-delimited segment
        ts_col = rows[-1].split("|")[1].strip()
        assert "T" in ts_col and "Z" in ts_col  # ISO-8601 UTC

    def test_detail_truncated_to_120_chars(self, tmp_path):
        audit = tmp_path / "work-audit.md"
        audit.write_text("| h |\n", encoding="utf-8")
        long_detail = "x" * 200
        write_ext_agent_event("EXT_AGENT_HEALTH", "a", long_detail, audit_file=audit)
        rows = _read_rows(audit)
        detail_col = rows[-1].split("|")[5].strip()
        assert len(detail_col) <= 120

    def test_no_op_when_file_missing(self, tmp_path):
        missing = tmp_path / "no-such-file.md"
        # Must not raise
        write_ext_agent_event("EXT_AGENT_ROUTED", "x", "detail", audit_file=missing)

    def test_multiple_events_appended_in_order(self, tmp_path):
        audit = tmp_path / "work-audit.md"
        audit.write_text("", encoding="utf-8")
        for i in range(3):
            write_ext_agent_event(f"EXT_AGENT_INVOKED", "a", f"call-{i}", audit_file=audit)
        rows = _read_rows(audit)
        assert len(rows) == 3
        assert "call-0" in rows[0]
        assert "call-2" in rows[2]

    def test_all_event_types_accepted(self, tmp_path):
        audit = tmp_path / "work-audit.md"
        audit.write_text("", encoding="utf-8")
        for etype in [
            "EXT_AGENT_ROUTED",
            "EXT_AGENT_INVOKED",
            "EXT_AGENT_INJECTION",
            "EXT_AGENT_UPDATE",
            "EXT_AGENT_HEALTH",
        ]:
            write_ext_agent_event(etype, "agent", "detail", audit_file=audit)
        content = audit.read_text(encoding="utf-8")
        assert content.count("EXT_AGENT_") == 5
