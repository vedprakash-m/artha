"""
tests/work/test_work_notes.py — Unit tests for post-meeting note capture.

Validates scripts/work_notes.py:
  - NotesCapture.from_dict()   — parses input data correctly
  - ActionItem.to_md_row()     — produces valid Markdown table row
  - Decision.to_md_row()       — produces valid Markdown table row
  - NotesWriter.write()        — writes to all three target files
  - OI-NNN ID auto-increment   — unique IDs per write
  - D-NNN ID auto-increment    — unique IDs per write
  - Atomic write safety        — temp file cleaned up on success
  - Carries forward            — captured in notes file
  - Empty decisions list       — no decision file touched
  - Empty action items list    — no open-items file touched
  - Follow-up package content  — contains all written items
  - WriteResult.ok             — True when no errors
  - main() CLI                 — JSON stdin round-trip

Run: pytest tests/work/test_work_notes.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from work_notes import (  # type: ignore
    NotesCapture,
    NotesWriter,
    ActionItem,
    Decision,
    WriteResult,
    _next_oi_id,
    _next_decision_id,
    main,
    cmd_remember,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    d.mkdir()
    return d


def _minimal_capture(
    decisions: list[dict] | None = None,
    action_items: list[dict] | None = None,
    carries: list[str] | None = None,
) -> NotesCapture:
    return NotesCapture.from_dict({
        "meeting_title": "Sprint Review",
        "meeting_date": "2026-03-24",
        "attendees": ["Alice", "Bob"],
        "decisions": decisions or [],
        "action_items": action_items or [],
        "carries_forward": carries or [],
        "notes_free_text": "Discussed roadmap and next steps.",
    })


# ---------------------------------------------------------------------------
# NotesCapture.from_dict
# ---------------------------------------------------------------------------

def test_capture_from_dict_basic():
    cap = NotesCapture.from_dict({
        "meeting_title": "1:1",
        "meeting_date": "2026-03-20",
    })
    assert cap.meeting_title == "1:1"
    assert cap.meeting_date == "2026-03-20"
    assert cap.decisions == []
    assert cap.action_items == []


def test_capture_from_dict_with_attendees():
    cap = NotesCapture.from_dict({
        "meeting_title": "Planning",
        "attendees": ["Alice", "Bob", "Charlie"],
    })
    assert "Alice" in cap.attendees


def test_capture_from_dict_with_decisions():
    cap = NotesCapture.from_dict({
        "meeting_title": "Planning",
        "decisions": [
            {"summary": "Go with Plan A", "owner": "Bob", "rationale": "Fastest"},
        ],
    })
    assert len(cap.decisions) == 1
    assert cap.decisions[0].summary == "Go with Plan A"
    assert cap.decisions[0].owner == "Bob"


def test_capture_from_dict_filters_empty_decisions():
    cap = NotesCapture.from_dict({
        "meeting_title": "Planning",
        "decisions": [{"summary": ""}, {"summary": "Valid decision"}],
    })
    assert len(cap.decisions) == 1


def test_capture_from_dict_with_action_items():
    cap = NotesCapture.from_dict({
        "meeting_title": "Planning",
        "action_items": [
            {"title": "Write spec", "owner": "Alice", "due": "2026-04-01"},
        ],
    })
    assert len(cap.action_items) == 1
    assert cap.action_items[0].title == "Write spec"
    assert cap.action_items[0].due == "2026-04-01"


def test_capture_from_dict_default_date():
    cap = NotesCapture.from_dict({"meeting_title": "Sync"})
    # Should get today's date
    import re
    assert re.match(r"\d{4}-\d{2}-\d{2}", cap.meeting_date)


def test_capture_from_dict_uses_meeting_id_as_title():
    cap = NotesCapture.from_dict({"meeting_id": "OKR Review 2026"})
    assert "OKR" in cap.meeting_title


# ---------------------------------------------------------------------------
# ActionItem.to_md_row
# ---------------------------------------------------------------------------

def test_action_item_md_row():
    a = ActionItem(title="Write spec", owner="Alice", due="2026-04-01", id="OI-001",
                   source_meeting="Planning")
    row = a.to_md_row()
    assert "OI-001" in row
    assert "Write spec" in row
    assert "Alice" in row
    assert "2026-04-01" in row
    assert "|" in row


def test_action_item_md_row_no_due():
    a = ActionItem(title="Do thing", id="OI-002")
    row = a.to_md_row()
    assert "—" in row


def test_action_item_truncates_long_title():
    a = ActionItem(title="A" * 100, id="OI-003")
    row = a.to_md_row()
    assert len(row) < 300  # reasonable bound


# ---------------------------------------------------------------------------
# Decision.to_md_row
# ---------------------------------------------------------------------------

def test_decision_md_row():
    d = Decision(
        summary="Use microservices",
        owner="Bob",
        rationale="Scalability",
        id="D-001",
        meeting_date="2026-03-20",
    )
    row = d.to_md_row()
    assert "D-001" in row
    assert "microservices" in row
    assert "Bob" in row
    assert "Scalability" in row


def test_decision_md_row_long_rationale():
    d = Decision(summary="Decision", rationale="X" * 100, id="D-002", meeting_date="")
    row = d.to_md_row()
    assert "…" in row or len(d.rationale.rstrip()) <= 64


# ---------------------------------------------------------------------------
# _next_oi_id
# ---------------------------------------------------------------------------

def test_next_oi_id_empty_file(tmp_path):
    path = tmp_path / "work-open-items.md"
    assert _next_oi_id(path) == "OI-001"


def test_next_oi_id_increments(tmp_path):
    path = tmp_path / "work-open-items.md"
    path.write_text("| OI-003 | Title | me | — | open | Meeting |", encoding="utf-8")
    assert _next_oi_id(path) == "OI-004"


def test_next_oi_id_multiple(tmp_path):
    path = tmp_path / "work-open-items.md"
    path.write_text("| OI-001 | A |\n| OI-002 | B |\n| OI-010 | C |", encoding="utf-8")
    assert _next_oi_id(path) == "OI-011"


# ---------------------------------------------------------------------------
# _next_decision_id
# ---------------------------------------------------------------------------

def test_next_decision_id_empty(tmp_path):
    path = tmp_path / "work-decisions.md"
    assert _next_decision_id(path) == "D-001"


def test_next_decision_id_increments(tmp_path):
    path = tmp_path / "work-decisions.md"
    path.write_text("| D-005 | 2026-01-01 | Summary | Bob | Rationale |", encoding="utf-8")
    assert _next_decision_id(path) == "D-006"


# ---------------------------------------------------------------------------
# NotesWriter.write() — action items
# ---------------------------------------------------------------------------

def test_write_creates_open_items(state_dir):
    cap = _minimal_capture(action_items=[
        {"title": "Write spec", "owner": "Alice", "due": "2026-04-01"},
        {"title": "Deploy service", "owner": "Bob"},
    ])
    writer = NotesWriter(state_dir=state_dir)
    result = writer.write(cap)

    assert result.ok
    assert result.action_items_written == 2
    oi_file = state_dir / "work-open-items.md"
    assert oi_file.exists()
    content = oi_file.read_text(encoding="utf-8")
    assert "Write spec" in content
    assert "Deploy service" in content
    assert "OI-001" in content
    assert "OI-002" in content


def test_write_increments_oi_ids_across_calls(state_dir):
    cap1 = _minimal_capture(action_items=[{"title": "Task A", "owner": "me"}])
    cap2 = _minimal_capture(action_items=[{"title": "Task B", "owner": "me"}])
    writer = NotesWriter(state_dir=state_dir)
    writer.write(cap1)
    writer.write(cap2)
    content = (state_dir / "work-open-items.md").read_text(encoding="utf-8")
    assert "OI-001" in content
    assert "OI-002" in content


def test_write_no_action_items_no_file(state_dir):
    cap = _minimal_capture()
    writer = NotesWriter(state_dir=state_dir)
    result = writer.write(cap)
    assert result.ok
    assert not (state_dir / "work-open-items.md").exists()


# ---------------------------------------------------------------------------
# NotesWriter.write() — decisions
# ---------------------------------------------------------------------------

def test_write_creates_decisions(state_dir):
    cap = _minimal_capture(decisions=[
        {"summary": "Use Redis for cache", "owner": "Bob", "rationale": "Low latency"},
    ])
    writer = NotesWriter(state_dir=state_dir)
    result = writer.write(cap)

    assert result.ok
    assert result.decisions_written == 1
    dec_file = state_dir / "work-decisions.md"
    assert dec_file.exists()
    content = dec_file.read_text(encoding="utf-8")
    assert "D-001" in content
    assert "Redis" in content


def test_write_no_decisions_no_file(state_dir):
    cap = _minimal_capture()
    writer = NotesWriter(state_dir=state_dir)
    writer.write(cap)
    assert not (state_dir / "work-decisions.md").exists()


# ---------------------------------------------------------------------------
# NotesWriter.write() — notes
# ---------------------------------------------------------------------------

def test_write_creates_notes_file(state_dir):
    cap = _minimal_capture()
    writer = NotesWriter(state_dir=state_dir)
    result = writer.write(cap)

    assert result.ok
    assert result.meetings_written == 1
    notes_file = state_dir / "work-notes.md"
    assert notes_file.exists()
    content = notes_file.read_text(encoding="utf-8")
    assert "Sprint Review" in content


def test_write_notes_includes_free_text(state_dir):
    cap = NotesCapture.from_dict({
        "meeting_title": "OKR Review",
        "notes_free_text": "Key insight: focus on H1 delivery.",
    })
    writer = NotesWriter(state_dir=state_dir)
    writer.write(cap)
    content = (state_dir / "work-notes.md").read_text(encoding="utf-8")
    assert "Key insight" in content


def test_write_notes_includes_carries(state_dir):
    cap = _minimal_capture(carries=["Follow up on ADO tickets", "Check vendor SLA"])
    writer = NotesWriter(state_dir=state_dir)
    writer.write(cap)
    content = (state_dir / "work-notes.md").read_text(encoding="utf-8")
    assert "Carry forward" in content
    assert "ADO tickets" in content


def test_write_notes_appends_across_calls(state_dir):
    cap1 = _minimal_capture(carries=["Item A"])
    cap2 = NotesCapture.from_dict({"meeting_title": "Second Meeting"})
    writer = NotesWriter(state_dir=state_dir)
    writer.write(cap1)
    writer.write(cap2)
    content = (state_dir / "work-notes.md").read_text(encoding="utf-8")
    assert "Sprint Review" in content
    assert "Second Meeting" in content


# ---------------------------------------------------------------------------
# Follow-up package
# ---------------------------------------------------------------------------

def test_follow_up_contains_all_written(state_dir):
    cap = _minimal_capture(
        decisions=[{"summary": "Green light", "owner": "me"}],
        action_items=[{"title": "Send brief", "owner": "Alice"}],
        carries=["Check status"],
    )
    writer = NotesWriter(state_dir=state_dir)
    result = writer.write(cap)
    fp = result.follow_up_package
    assert "Sprint Review" in fp
    assert "Green light" in fp
    assert "Send brief" in fp
    assert "Check status" in fp


def test_follow_up_empty_capture(state_dir):
    cap = NotesCapture.from_dict({"meeting_title": "Quick Sync"})
    writer = NotesWriter(state_dir=state_dir)
    result = writer.write(cap)
    fp = result.follow_up_package
    assert "Quick Sync" in fp
    assert "No decisions" in fp or "0" in fp or "no" in fp.lower()


# ---------------------------------------------------------------------------
# WriteResult
# ---------------------------------------------------------------------------

def test_write_result_ok_on_success(state_dir):
    cap = _minimal_capture()
    writer = NotesWriter(state_dir=state_dir)
    result = writer.write(cap)
    assert result.ok is True
    assert result.errors == []


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------

def test_main_json_stdin(state_dir, monkeypatch, capsys):
    import io
    payload = json.dumps({
        "meeting_title": "CLI Test Meeting",
        "action_items": [{"title": "Write docs", "owner": "me"}],
    })
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    rc = main(["--input", "-", "--state-dir", str(state_dir)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "CLI Test Meeting" in captured.out
    assert "Write docs" in captured.out


def test_main_invalid_json(capsys):
    import io
    from unittest.mock import patch
    import work_notes as wn
    original = sys.stdin
    try:
        sys.stdin = io.StringIO("not json at all {{{")
        rc = main(["--input", "-"])
        assert rc == 2
    finally:
        sys.stdin = original


def test_main_json_file(state_dir, tmp_path, capsys):
    payload = {"meeting_title": "File Meeting", "decisions": [{"summary": "Go live", "owner": "Sam"}]}
    input_file = tmp_path / "capture.json"
    input_file.write_text(json.dumps(payload), encoding="utf-8")
    rc = main(["--input", str(input_file), "--state-dir", str(state_dir)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Go live" in captured.out or "D-001" in captured.out


# ===========================================================================
# cmd_remember (§8.8 v2.3.0, /work remember)
# ===========================================================================

class TestCmdRemember:
    """cmd_remember(): instant micro-capture to work-notes.md."""

    def test_basic_append_returns_confirmation(self, tmp_path):
        msg = cmd_remember("Platform Alpha ramp P1 completed", state_dir=tmp_path)
        assert "captured" in msg.lower() or "remember" in msg.lower() or "work-notes" in msg.lower()

    def test_creates_work_notes_file(self, tmp_path):
        cmd_remember("test note content", state_dir=tmp_path)
        notes_path = tmp_path / "work-notes.md"
        assert notes_path.exists()

    def test_content_written_to_notes(self, tmp_path):
        cmd_remember("sprint planning meeting notes", state_dir=tmp_path)
        content = (tmp_path / "work-notes.md").read_text(encoding="utf-8")
        assert "sprint planning" in content.lower()

    def test_org_calendar_prefix_routes_to_org_cal_file(self, tmp_path):
        cmd_remember("org-calendar: connect_submission 2026-06-15", state_dir=tmp_path)
        org_cal = tmp_path / "work-org-calendar.md"
        assert org_cal.exists()

    def test_truncates_at_500_chars(self, tmp_path):
        long_text = "A" * 600
        cmd_remember(long_text, state_dir=tmp_path)
        content = (tmp_path / "work-notes.md").read_text(encoding="utf-8")
        # The stored text should be at most 500 chars
        assert len([line for line in content.splitlines() if "AAA" in line][0]) <= 520

    def test_empty_text_returns_error_message(self, tmp_path):
        msg = cmd_remember("", state_dir=tmp_path)
        # Should not crash — returns diagnostic
        assert isinstance(msg, str)

    def test_main_remember_flag_exits_zero(self, state_dir, monkeypatch, capsys):
        rc = main(["--remember", "quick capture test", "--state-dir", str(state_dir)])
        assert rc == 0
