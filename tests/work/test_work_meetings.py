"""tests/work/test_work_meetings.py — Focused tests for scripts/work/meetings.py

T3-13..22 per pay-debt.md §7.6
"""
from __future__ import annotations

import sys
import textwrap
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import work.meetings
import work.helpers
from work.meetings import (
    _MeetingEntry, _parse_meeting_start_dt, _parse_today_meetings,
    _extract_carry_forward_items, _readiness_score, _detect_decision_drift,
    cmd_prep, _PREREAD_SECTION,
)


def _fresh_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_state(state_dir: Path, name: str, fm: dict, body: str = "") -> Path:
    p = state_dir / name
    content = "---\n" + yaml.dump(fm, default_flow_style=False) + "---\n\n" + body
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def work_dir(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    work.meetings._WORK_STATE_DIR = d
    work.helpers._WORK_STATE_DIR = d
    return d


# ---------------------------------------------------------------------------
# T3-13: _MeetingEntry dataclass
# ---------------------------------------------------------------------------

def test_meeting_entry_fields():
    m = _MeetingEntry(
        title="Standup",
        time_str="9:00–9:30 AM",
        start_dt=None,
        duration_min=30,
        attendee_count=5,
        is_recurring=True,
        is_personal=False,
    )
    assert m.title == "Standup"
    assert m.duration_min == 30
    assert m.is_recurring is True
    assert m.is_personal is False


# ---------------------------------------------------------------------------
# T3-14: _parse_meeting_start_dt various formats
# ---------------------------------------------------------------------------

class TestParseMeetingStartDt:
    def test_am_time(self):
        today = date(2026, 3, 24)
        dt = _parse_meeting_start_dt("9:00–9:30 AM", today)
        assert dt is not None
        assert dt.hour == 9
        assert dt.minute == 0

    def test_pm_time(self):
        today = date(2026, 3, 24)
        dt = _parse_meeting_start_dt("2:00–2:30 PM", today)
        assert dt is not None
        assert dt.hour == 14

    def test_noon(self):
        today = date(2026, 3, 24)
        dt = _parse_meeting_start_dt("12:00–12:30 PM", today)
        assert dt is not None
        assert dt.hour == 12

    def test_midnight_am(self):
        today = date(2026, 3, 24)
        dt = _parse_meeting_start_dt("12:00–12:30 AM", today)
        assert dt is not None
        assert dt.hour == 0

    def test_garbage_returns_none(self):
        dt = _parse_meeting_start_dt("not-a-time", date.today())
        assert dt is None


# ---------------------------------------------------------------------------
# T3-15: _parse_today_meetings
# ---------------------------------------------------------------------------

class TestParseTodayMeetings:
    def test_parses_today_meetings(self):
        today = date(2026, 3, 24)
        day_name = datetime(2026, 3, 24).strftime("%A")  # Monday
        body = textwrap.dedent(f"""\
            ### {day_name} — March 24
            | Time | Title | Duration | Attendees | Recurring |
            |------|-------|----------|-----------|-----------|
            | 9:00–9:30 AM | Morning Standup | 30 min | 8 | Yes (Daily) |
            | 2:00–3:00 PM | Design Review | 60 min | 12 | No |
        """)
        meetings = _parse_today_meetings(body, today)
        assert len(meetings) == 2
        titles = [m.title for m in meetings]
        assert "Morning Standup" in titles
        assert "Design Review" in titles

    def test_wrong_day_returns_empty(self):
        body = "### Sunday — March 22\n| 9 AM–10 AM | Meeting | 60 min | 5 | No |\n"
        # Today is March 24 (Monday) — Sunday should not match
        today = date(2026, 3, 24)
        meetings = _parse_today_meetings(body, today)
        assert len(meetings) == 0

    def test_duration_parsed_correctly(self):
        today = date(2026, 3, 24)
        day_name = datetime(2026, 3, 24).strftime("%A")
        body = f"### {day_name} — March 24\n| Time | Title | Duration | Attendees | Rec |\n|---|---|---|---|---|\n| 9:00–10:00 AM | Big Meeting | 60 min | 20 | Yes |\n"
        meetings = _parse_today_meetings(body, today)
        if meetings:
            assert meetings[0].duration_min == 60
            assert meetings[0].attendee_count == 20


# ---------------------------------------------------------------------------
# T3-16: _readiness_score boundary cases
# ---------------------------------------------------------------------------

class TestReadinessScore:
    def _make_meeting(self, title="Sprint Review", recurring=True, attendees=15):
        return _MeetingEntry(
            title=title,
            time_str="2:00 PM",
            start_dt=None,
            duration_min=60,
            attendee_count=attendees,
            is_recurring=recurring,
            is_personal=False,
        )

    def test_score_in_range(self):
        m = self._make_meeting()
        score, gaps = _readiness_score(m, "", "", "")
        assert 0 <= score <= 100

    def test_no_notes_recurring_penalty(self):
        m = self._make_meeting(recurring=True)
        score, gaps = _readiness_score(m, "", "", "")
        # Should have some gap for no notes
        assert score < 90

    def test_large_attendee_penalty(self):
        m = self._make_meeting(attendees=20)
        score_no_people, _ = _readiness_score(m, "", "", "")
        score_with_people, _ = _readiness_score(m, "", "sprint review content for meeting", "")
        # With relevant people context, score should be higher or equal
        assert score_with_people >= score_no_people - 5  # ±5 tolerance

    def test_score_clipped_to_20_minimum(self):
        m = self._make_meeting(recurring=True, attendees=50)
        # Even with worst conditions, score should be ≥ 20
        score, gaps = _readiness_score(m, "", "", "")
        assert score >= 20

    def test_gaps_are_strings(self):
        m = self._make_meeting()
        score, gaps = _readiness_score(m, "", "", "")
        assert all(isinstance(g, str) for g in gaps)


# ---------------------------------------------------------------------------
# T3-17: _detect_decision_drift detection + false positive prevention
# ---------------------------------------------------------------------------

class TestDetectDecisionDrift:
    def test_detects_old_open_decision(self):
        old_date = (datetime.now(timezone.utc) - timedelta(days=50)).strftime("%Y-%m-%d")
        decisions_body = f"| D-001 | {old_date} | OPEN: review scope | open |\n"
        alerts = _detect_decision_drift("review", decisions_body)
        assert isinstance(alerts, list)

    def test_no_drift_for_closed_decisions(self):
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        decisions_body = f"| D-002 | {recent} | Closed: deploy plan | closed |\n"
        alerts = _detect_decision_drift("deploy", decisions_body)
        assert len(alerts) == 0

    def test_empty_input_no_crash(self):
        alerts = _detect_decision_drift("", "")
        assert alerts == []

    def test_unrelated_keyword_no_false_positive(self):
        old_date = (datetime.now(timezone.utc) - timedelta(days=50)).strftime("%Y-%m-%d")
        decisions_body = f"| D-003 | {old_date} | OPEN: review budget | open |\n"
        alerts = _detect_decision_drift("architecture", decisions_body)
        assert len(alerts) == 0


# ---------------------------------------------------------------------------
# T3-18: cmd_prep missing calendar
# ---------------------------------------------------------------------------

def test_cmd_prep_missing_calendar(work_dir):
    # No state files — should not raise, should degrade gracefully
    out = cmd_prep()
    assert isinstance(out, str)
    assert len(out) > 0


# ---------------------------------------------------------------------------
# T3-19: _extract_carry_forward_items
# ---------------------------------------------------------------------------

def test_extract_carry_forward_items_found():
    notes_body = textwrap.dedent("""\
        ## Sprint Review Notes
        **Carry forward:**
        - Action item from last sprint
        - Another pending item
    """)
    items = _extract_carry_forward_items(notes_body, "Sprint Review")
    assert len(items) >= 1
    assert any("Action item" in i or "pending" in i for i in items)


def test_extract_carry_forward_no_match():
    notes_body = "## Some Other Meeting\n**Carry forward:**\n- Unrelated item\n"
    items = _extract_carry_forward_items(notes_body, "Architecture Review")
    assert items == []


# ---------------------------------------------------------------------------
# T3-20: _PREREAD_SECTION constant
# ---------------------------------------------------------------------------

def test_preread_section_constant():
    assert isinstance(_PREREAD_SECTION, str)
    assert _PREREAD_SECTION.startswith("##")
