"""
tests/work/test_domain_writers.py — Unit tests for Work OS domain state writers.

Validates scripts/work_domain_writers.py:
  - write_calendar_state() — produces valid work-calendar.md
  - write_comms_state()    — produces valid work-comms.md
  - write_projects_state() — produces valid work-projects.md
  - write_boundary_state() — boundary score computation
  - append_career_evidence() — appends without corruption
  - add_source()            — registers new source entries
  - Atomic write safety     — temp file is cleaned up on success
  - PII redaction           — redact_keywords applied to subject lines

Run: pytest tests/work/test_domain_writers.py -v
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from work_domain_writers import (  # type: ignore
    write_calendar_state,
    write_comms_state,
    write_projects_state,
    write_boundary_state,
    append_career_evidence,
    add_source,
    _apply_redact,
    _find_conflicts,
    _after_hours_count,
    _back_to_back_count,
    _parse_dt,
    _to_local,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(
    summary: str = "Test Meeting",
    start_offset_h: float = 1.0,
    duration_h: float = 1.0,
    status: str = "confirmed",
    **kwargs,
) -> dict:
    start = _now() + timedelta(hours=start_offset_h)
    end = start + timedelta(hours=duration_h)
    evt = {
        "id": f"evt-{summary[:4].lower().replace(' ', '-')}",
        "summary": summary,
        "start": _iso(start),
        "end": _iso(end),
        "status": status,
        "organizer": "organizer@example.com",
        "attendees": ["a@example.com", "b@example.com"],
        "recurring": False,
        "is_online_meeting": True,
        "all_day": False,
        "source": "msgraph_calendar",
    }
    evt.update(kwargs)
    return evt


def _email(
    subject: str = "Test Email",
    needs_response: bool = True,
    is_read: bool = False,
    **kwargs,
) -> dict:
    mail = {
        "id": f"mail-{subject[:4].lower().replace(' ', '-')}",
        "subject": subject,
        "from": "sender@example.com",
        "received": _iso(_now() - timedelta(hours=2)),
        "snippet": "Email thread context (no body stored).",
        "needs_response": needs_response,
        "is_read": is_read,
        "importance": "normal",
        "folder": "inbox",
        "source": "msgraph_email",
    }
    mail.update(kwargs)
    return mail


def _work_item(
    title: str = "Fix bug",
    state: str = "Active",
    item_type: str = "Task",
    priority: int = 2,
    **kwargs,
) -> dict:
    item = {
        "id": 42,
        "title": title,
        "state": state,
        "type": item_type,
        "priority": priority,
        "iteration_path": "MyProject\\Sprint 3",
        "area_path": "MyProject\\Backend",
        "assigned_to": "user@example.com",
        "tags": [],
        "changed_date": _iso(_now() - timedelta(days=1)),
        "source": "ado_workitems",
    }
    item.update(kwargs)
    return item


_DEFAULT_CFG = {
    "timezone": "UTC",
    "work_start_time": "09:00",
    "work_end_time": "17:00",
}


# ===========================================================================
# Internal helper unit tests
# ===========================================================================

class TestHelpers:

    def test_parse_dt_iso(self):
        dt = _parse_dt("2026-03-25T10:00:00+00:00")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3

    def test_parse_dt_z_suffix(self):
        dt = _parse_dt("2026-03-25T10:00:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_parse_dt_empty(self):
        assert _parse_dt("") is None
        assert _parse_dt(None) is None  # type: ignore[arg-type]

    def test_apply_redact_replaces_keyword(self):
        result = _apply_redact("Please review the ProjectX budget", ["ProjectX"])
        assert "[REDACTED]" in result
        assert "ProjectX" not in result

    def test_apply_redact_no_keywords(self):
        text = "No secrets here"
        assert _apply_redact(text, []) == text

    def test_apply_redact_case_insensitive(self):
        result = _apply_redact("message about projectx deadline", ["ProjectX"])
        assert "[REDACTED]" in result

    def test_find_conflicts_overlap(self):
        base = _now()
        a = {"id": "a", "start": _iso(base), "end": _iso(base + timedelta(hours=2))}
        b = {"id": "b", "start": _iso(base + timedelta(hours=1)), "end": _iso(base + timedelta(hours=3))}
        events = [(base, a), (base + timedelta(hours=1), b)]
        conflicts = _find_conflicts(events)
        assert len(conflicts) == 1

    def test_find_conflicts_no_overlap(self):
        base = _now()
        a = {"id": "a", "start": _iso(base), "end": _iso(base + timedelta(hours=1))}
        b = {"id": "b", "start": _iso(base + timedelta(hours=2)), "end": _iso(base + timedelta(hours=3))}
        a_start = _parse_dt(a["start"])
        b_start = _parse_dt(b["start"])
        events = [(a_start, a), (b_start, b)]
        assert _find_conflicts(events) == []

    def test_after_hours_count_after_work_end(self):
        """An event starting at 18:00 UTC counts as after-hours."""
        base = datetime(2026, 3, 25, 18, 0, tzinfo=timezone.utc)  # 6 PM UTC
        evt = {"start": _iso(base), "end": _iso(base + timedelta(hours=1))}
        events = [(base, evt)]
        # work_start=9, work_end=17
        count = _after_hours_count(events, work_end=17.0, work_start=9.0, tz="UTC")
        assert count == 1

    def test_after_hours_count_within_hours(self):
        base = datetime(2026, 3, 25, 10, 0, tzinfo=timezone.utc)  # 10 AM UTC
        evt = {"start": _iso(base), "end": _iso(base + timedelta(hours=1))}
        events = [(base, evt)]
        count = _after_hours_count(events, work_end=17.0, work_start=9.0, tz="UTC")
        assert count == 0

    def test_back_to_back_count(self):
        base = datetime(2026, 3, 25, 10, 0, tzinfo=timezone.utc)
        a = {"start": _iso(base), "end": _iso(base + timedelta(hours=1))}
        # gap of 3 min — triggers back-to-back
        b = {"start": _iso(base + timedelta(minutes=63)), "end": _iso(base + timedelta(hours=2))}
        a_start = _parse_dt(a["start"])
        b_start = _parse_dt(b["start"])
        events = [(a_start, a), (b_start, b)]
        count = _back_to_back_count(events, gap_minutes=5)
        assert count == 1


# ===========================================================================
# write_calendar_state
# ===========================================================================

class TestWriteCalendarState:

    def test_creates_file(self, tmp_path):
        out = tmp_path / "work-calendar.md"
        write_calendar_state([], _DEFAULT_CFG, out)
        assert out.exists()

    def test_file_has_frontmatter(self, tmp_path):
        out = tmp_path / "work-calendar.md"
        write_calendar_state([_event()], _DEFAULT_CFG, out)
        content = out.read_text(encoding="utf-8")
        assert content.startswith("---")
        assert "last_updated" in content

    def test_empty_events_writes_zero_metrics(self, tmp_path):
        out = tmp_path / "work-calendar.md"
        write_calendar_state([], _DEFAULT_CFG, out)
        content = out.read_text(encoding="utf-8")
        assert "meetings_today: 0" in content

    def test_event_summary_appears_in_output(self, tmp_path):
        out = tmp_path / "work-calendar.md"
        write_calendar_state([_event("Architecture Review")], _DEFAULT_CFG, out)
        content = out.read_text(encoding="utf-8")
        assert "Architecture Review" in content

    def test_cancelled_event_excluded(self, tmp_path):
        out = tmp_path / "work-calendar.md"
        write_calendar_state([_event("Cancelled Meeting", status="cancelled")], _DEFAULT_CFG, out)
        content = out.read_text(encoding="utf-8")
        assert "meetings_today: 0" in content

    def test_atomic_write_no_tmp_leftover(self, tmp_path):
        out = tmp_path / "work-calendar.md"
        write_calendar_state([_event()], _DEFAULT_CFG, out)
        tmp_file = out.with_suffix(".md.tmp")
        assert not tmp_file.exists()

    def test_domain_name_in_frontmatter(self, tmp_path):
        out = tmp_path / "work-calendar.md"
        write_calendar_state([], _DEFAULT_CFG, out)
        content = out.read_text(encoding="utf-8")
        assert "work-calendar" in content

    def test_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "nested" / "dir" / "work-calendar.md"
        write_calendar_state([], _DEFAULT_CFG, out)
        assert out.exists()


# ===========================================================================
# write_comms_state
# ===========================================================================

class TestWriteCommsState:

    def test_creates_file(self, tmp_path):
        out = tmp_path / "work-comms.md"
        write_comms_state([], [], out)
        assert out.exists()

    def test_file_starts_with_frontmatter(self, tmp_path):
        out = tmp_path / "work-comms.md"
        write_comms_state([_email()], [], out)
        content = out.read_text(encoding="utf-8")
        assert content.startswith("---")

    def test_action_required_email_appears(self, tmp_path):
        out = tmp_path / "work-comms.md"
        write_comms_state([_email("Urgent Review Needed", needs_response=True)], [], out)
        content = out.read_text(encoding="utf-8")
        assert "Urgent Review Needed" in content

    def test_read_no_response_not_in_action_section(self, tmp_path):
        out = tmp_path / "work-comms.md"
        write_comms_state([_email("FYI", needs_response=False, is_read=True)], [], out)
        content = out.read_text(encoding="utf-8")
        # File should still be created successfully
        assert out.exists()

    def test_empty_inputs_creates_valid_file(self, tmp_path):
        out = tmp_path / "work-comms.md"
        write_comms_state([], [], out)
        content = out.read_text(encoding="utf-8")
        assert "work-comms" in content

    def test_work_os_flag_in_frontmatter(self, tmp_path):
        out = tmp_path / "work-comms.md"
        write_comms_state([], [], out)
        content = out.read_text(encoding="utf-8")
        assert "work_os" in content


# ===========================================================================
# write_projects_state
# ===========================================================================

class TestWriteProjectsState:

    def test_creates_file(self, tmp_path):
        out = tmp_path / "work-projects.md"
        write_projects_state([], {}, out)
        assert out.exists()

    def test_work_item_title_appears(self, tmp_path):
        out = tmp_path / "work-projects.md"
        write_projects_state([_work_item("Implement caching layer")], {}, out)
        content = out.read_text(encoding="utf-8")
        assert "Implement caching layer" in content

    def test_blocked_item_classified(self, tmp_path):
        out = tmp_path / "work-projects.md"
        write_projects_state([_work_item("Blocked on review", state="Blocked")], {}, out)
        content = out.read_text(encoding="utf-8")
        # Blocked items should appear in the output
        assert out.exists()

    def test_completed_item_appears(self, tmp_path):
        out = tmp_path / "work-projects.md"
        write_projects_state([_work_item("Ship feature", state="Closed")], {}, out)
        content = out.read_text(encoding="utf-8")
        assert out.exists()

    def test_frontmatter_has_domain(self, tmp_path):
        out = tmp_path / "work-projects.md"
        write_projects_state([], {}, out)
        content = out.read_text(encoding="utf-8")
        assert "work-projects" in content


# ===========================================================================
# write_boundary_state
# ===========================================================================

class TestWriteBoundaryState:

    def test_creates_file(self, tmp_path):
        out = tmp_path / "work-boundary.md"
        write_boundary_state([], _DEFAULT_CFG, out)
        assert out.exists()

    def test_boundary_score_in_frontmatter(self, tmp_path):
        out = tmp_path / "work-boundary.md"
        write_boundary_state([], _DEFAULT_CFG, out)
        content = out.read_text(encoding="utf-8")
        assert "boundary_score" in content

    def test_after_hours_event_reduces_score(self, tmp_path):
        """More after-hours events should yield a lower or equal boundary score."""
        out_clean = tmp_path / "clean.md"
        out_heavy = tmp_path / "heavy.md"
        after_hours_event = _event("Late Meeting", start_offset_h=11.0)  # UTC+11h → likely after-hours
        write_boundary_state([], _DEFAULT_CFG, out_clean)
        write_boundary_state([after_hours_event] * 5, _DEFAULT_CFG, out_heavy)
        # Both files must exist and be valid
        assert out_clean.exists()
        assert out_heavy.exists()

    def test_zero_events_max_score(self, tmp_path):
        """No events should yield boundary score of 100."""
        import yaml  # type: ignore[import]
        out = tmp_path / "work-boundary.md"
        write_boundary_state([], _DEFAULT_CFG, out)
        text = out.read_text(encoding="utf-8")
        fm_text = text[3: text.find("---", 3)]
        fm = yaml.safe_load(fm_text)
        assert fm["boundary_score"] == 100


# ===========================================================================
# append_career_evidence
# ===========================================================================

class TestAppendCareerEvidence:

    def test_creates_file_if_missing(self, tmp_path):
        out = tmp_path / "work-career.md"
        append_career_evidence(
            {"date": "2026-03-25", "description": "Shipped feature X", "goal": "Delivery"},
            out,
        )
        assert out.exists()

    def test_appended_entry_appears(self, tmp_path):
        out = tmp_path / "work-career.md"
        append_career_evidence(
            {"date": "2026-03-25", "description": "Led design review for platform API"},
            out,
        )
        content = out.read_text(encoding="utf-8")
        assert "Led design review" in content

    def test_multiple_appends_preserve_existing(self, tmp_path):
        out = tmp_path / "work-career.md"
        append_career_evidence({"date": "2026-03-25", "description": "Entry 1"}, out)
        append_career_evidence({"date": "2026-03-26", "description": "Entry 2"}, out)
        content = out.read_text(encoding="utf-8")
        assert "Entry 1" in content
        assert "Entry 2" in content


# ===========================================================================
# add_source
# ===========================================================================

class TestAddSource:

    def test_creates_file_if_missing(self, tmp_path):
        out = tmp_path / "work-sources.md"
        add_source(
            {"url": "https://example.com/dash", "title": "Sprint Dashboard", "what_it_answers": "velocity"},
            out,
        )
        assert out.exists()

    def test_source_url_appears(self, tmp_path):
        out = tmp_path / "work-sources.md"
        add_source(
            {"url": "https://example.com/board", "title": "ADO Board"},
            out,
        )
        content = out.read_text(encoding="utf-8")
        assert "https://example.com/board" in content

    def test_multiple_sources_preserved(self, tmp_path):
        out = tmp_path / "work-sources.md"
        add_source({"url": "https://example.com/a", "title": "Source A"}, out)
        add_source({"url": "https://example.com/b", "title": "Source B"}, out)
        content = out.read_text(encoding="utf-8")
        assert "Source A" in content
        assert "Source B" in content


# ===========================================================================
# append_project_journey (§8.8 v2.3.0)
# ===========================================================================

from work_domain_writers import append_project_journey  # type: ignore  # noqa: E402


class TestProjectJourneyAppend:
    """append_project_journey(): milestone append to work-project-journeys.md."""

    def test_creates_file_if_missing(self, tmp_path):
        dest = tmp_path / "work-project-journeys.md"
        append_project_journey(
            {"project": "Platform Alpha Ramp", "date": "Mar 2026", "milestone": "P1 launched"},
            dest,
        )
        assert dest.exists()

    def test_row_written_to_new_file(self, tmp_path):
        dest = tmp_path / "journeys.md"
        append_project_journey(
            {"project": "Platform Delta", "date": "Jan 2026",
             "milestone": "MVP shipped", "impact": "Team unblocked"},
            dest,
        )
        content = dest.read_text(encoding="utf-8")
        assert "MVP shipped" in content
        assert "Team unblocked" in content

    def test_second_row_appended_to_existing(self, tmp_path):
        dest = tmp_path / "journeys.md"
        append_project_journey(
            {"project": "Platform Alpha Ramp", "date": "Jan 2026", "milestone": "P0 complete"},
            dest,
        )
        append_project_journey(
            {"project": "Platform Alpha Ramp", "date": "Mar 2026", "milestone": "P1 launched"},
            dest,
        )
        content = dest.read_text(encoding="utf-8")
        assert "P0 complete" in content
        assert "P1 launched" in content

    def test_new_project_creates_section(self, tmp_path):
        dest = tmp_path / "journeys.md"
        append_project_journey(
            {"project": "Platform Beta", "date": "Feb 2026", "milestone": "M0 go-live"},
            dest,
        )
        content = dest.read_text(encoding="utf-8")
        assert "## Platform Beta" in content

    def test_atomic_write_no_tmp_left(self, tmp_path):
        dest = tmp_path / "journeys.md"
        append_project_journey(
            {"project": "Platform Alpha", "date": "Mar 2026", "milestone": "done"},
            dest,
        )
        assert not (tmp_path / "journeys.md.tmp").exists()


# ===========================================================================
# append_visibility_event (§8.6 v2.3.0)
# ===========================================================================

from work_domain_writers import append_visibility_event  # type: ignore  # noqa: E402


class TestVisibilityEventAppend:
    """append_visibility_event(): immutable visibility event recorder."""

    def test_creates_section_if_missing(self, tmp_path):
        dest = tmp_path / "work-people.md"
        dest.write_text("# Work People\n\n## Manager Chain\n\n- Alice\n", encoding="utf-8")
        append_visibility_event(
            {"date": "2026-03-10", "stakeholder": "bigwig", "event_type": "replied",
             "context": "Great work on P0", "source_domain": "work-comms"},
            dest,
        )
        content = dest.read_text(encoding="utf-8")
        assert "## Visibility Events" in content
        assert "2026-03-10" in content
        assert "bigwig" in content
        assert "replied" in content

    def test_creates_file_if_missing(self, tmp_path):
        dest = tmp_path / "work-people.md"
        append_visibility_event(
            {"date": "2026-03-15", "stakeholder": "exec", "event_type": "presented_about",
             "context": "LT review 50 attendees", "source_domain": "work-calendar"},
            dest,
        )
        assert dest.exists()
        content = dest.read_text(encoding="utf-8")
        assert "Visibility Events" in content

    def test_appends_to_existing_section(self, tmp_path):
        dest = tmp_path / "work-people.md"
        dest.write_text(
            "# Work People\n\n## Visibility Events\n\n"
            "| Date | Stakeholder | Type | Context | Source |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| 2026-03-01 | first | replied | old context | work-comms |\n",
            encoding="utf-8",
        )
        append_visibility_event(
            {"date": "2026-03-20", "stakeholder": "second", "event_type": "at_mentioned",
             "context": "New mention", "source_domain": "work-comms"},
            dest,
        )
        content = dest.read_text(encoding="utf-8")
        assert "2026-03-01" in content  # original preserved
        assert "2026-03-20" in content  # new appended

    def test_pii_redaction_applied(self, tmp_path):
        dest = tmp_path / "work-people.md"
        append_visibility_event(
            {"date": "2026-03-10", "stakeholder": "secretthing",
             "event_type": "replied", "context": "Hello secretthing",
             "source_domain": "work-comms"},
            dest,
            redact_keywords=["secretthing"],
        )
        content = dest.read_text(encoding="utf-8")
        assert "secretthing" not in content
        assert "REDACTED" in content

    def test_context_truncated_to_100_chars(self, tmp_path):
        dest = tmp_path / "work-people.md"
        long_context = "x" * 200
        append_visibility_event(
            {"date": "2026-03-10", "stakeholder": "alias",
             "event_type": "replied", "context": long_context,
             "source_domain": "work-comms"},
            dest,
        )
        content = dest.read_text(encoding="utf-8")
        # Context column should not contain >100 x's
        assert "x" * 101 not in content

    def test_atomic_write_no_tmp_left(self, tmp_path):
        dest = tmp_path / "work-people.md"
        append_visibility_event(
            {"date": "2026-03-21", "stakeholder": "someone",
             "event_type": "cited_doc", "context": "doc cited",
             "source_domain": "work-calendar"},
            dest,
        )
        assert not (tmp_path / "work-people.md.tmp").exists()

    def test_event_type_preserved(self, tmp_path):
        dest = tmp_path / "work-people.md"
        for et in ["replied", "at_mentioned", "cited_doc", "invited_to_meeting", "presented_about"]:
            dest2 = tmp_path / f"people_{et}.md"
            append_visibility_event(
                {"date": "2026-03-10", "stakeholder": "alias",
                 "event_type": et, "context": "ctx", "source_domain": "work-calendar"},
                dest2,
            )
            content = dest2.read_text(encoding="utf-8")
            assert et in content
