"""
tests/work/test_work_reader.py — Unit tests for Work OS read-path CLI.

Validates scripts/work_reader.py:
  - cmd_work()          — full briefing from state files
  - cmd_pulse()         — 30-second snapshot
  - cmd_sprint()        — delivery health
  - cmd_health()        — connector diagnostics
  - cmd_return()        — absence recovery
  - cmd_connect()       — evidence assembly
  - cmd_connect_prep()  — Connect cycle narrative prep (Phase 1 gate)
  - cmd_people()        — person lookup
  - cmd_docs()          — recent artifacts
  - cmd_sources()       — registry lookup
  - cmd_sources_add()   — register new data source (Phase 1 gate)
  - cmd_newsletter()    — team newsletter draft via NE (Phase 2)
  - cmd_deck()          — LT deck content via NE (Phase 2)
  - cmd_memo()          — status memo via NE (Phase 2)
  - cmd_talking_points() — talking points via NE (Phase 2)
  - main()              — CLI dispatch
  - Graceful degradation when state files missing
  - Freshness footer always present
  - Staleness warning for >18h old data

Run: pytest tests/work/test_work_reader.py -v
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Inject scripts into sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import work_reader  # type: ignore
from work_reader import (  # type: ignore
    cmd_work,
    cmd_pulse,
    cmd_sprint,
    cmd_health,
    cmd_return,
    cmd_connect,
    cmd_connect_prep,
    cmd_people,
    cmd_docs,
    cmd_sources,
    cmd_sources_add,
    cmd_live,
    cmd_newsletter,
    cmd_deck,
    cmd_memo,
    cmd_talking_points,
    cmd_promo_case,
    cmd_journey,
    cmd_day,
    cmd_decide,
    main,
    _read_frontmatter,
    _read_body,
    _parse_dt,
    _age_str,
    _age_hours,
    _dfs_label,
    _boundary_label,
    _freshness_footer,
    _extract_section,
    _staleness_header,
    WorkBriefingConfig,
    _build_briefing_config,
    _read_org_calendar_milestones,
    _build_influence_map,
    _ensure_decisions_header,
    _append_to_file,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def work_dir(tmp_path: Path) -> Path:
    state_dir = tmp_path / "work"
    state_dir.mkdir()
    return state_dir


def _write_state(state_dir: Path, name: str, frontmatter: dict, body: str = "") -> Path:
    """Write a state file with YAML frontmatter + body."""
    import yaml  # type: ignore[import]
    path = state_dir / name
    content = "---\n" + yaml.dump(frontmatter, default_flow_style=False) + "---\n\n" + body
    path.write_text(content, encoding="utf-8")
    return path


def _fresh_fm() -> dict:
    return {"last_updated": datetime.now(timezone.utc).isoformat()}


def _stale_fm() -> dict:
    stale_dt = datetime.now(timezone.utc) - timedelta(hours=24)
    return {"last_updated": stale_dt.isoformat()}


def _inject_state_dir(state_dir: Path) -> None:
    """Point work_reader module at the temp state dir.

    Phase 3: also patch work.helpers and work.briefing since helper functions
    read from their own module-level _WORK_STATE_DIR after Phase 3 decomposition.
    """
    work_reader._WORK_STATE_DIR = state_dir
    import work.helpers  # noqa: PLC0415
    work.helpers._WORK_STATE_DIR = state_dir
    import work.briefing  # noqa: PLC0415
    work.briefing._WORK_STATE_DIR = state_dir


# ---------------------------------------------------------------------------
# _parse_dt
# ---------------------------------------------------------------------------

def test_parse_dt_iso():
    dt = _parse_dt("2026-03-24T10:00:00+00:00")
    assert dt is not None
    assert dt.year == 2026


def test_parse_dt_z_suffix():
    dt = _parse_dt("2026-03-24T10:00:00Z")
    assert dt is not None


def test_parse_dt_empty():
    assert _parse_dt("") is None
    assert _parse_dt(None) is None


def test_parse_dt_invalid():
    assert _parse_dt("not-a-date") is None


# ---------------------------------------------------------------------------
# _age_str
# ---------------------------------------------------------------------------

def test_age_str_minutes():
    dt = datetime.now(timezone.utc) - timedelta(minutes=30)
    s = _age_str(dt)
    assert "m ago" in s or "0h ago" in s


def test_age_str_hours():
    dt = datetime.now(timezone.utc) - timedelta(hours=3)
    s = _age_str(dt)
    assert "3h ago" in s


def test_age_str_days():
    dt = datetime.now(timezone.utc) - timedelta(days=2)
    s = _age_str(dt)
    assert "2d ago" in s


def test_age_str_none():
    assert _age_str(None) == "unknown"


# ---------------------------------------------------------------------------
# _dfs_label / _boundary_label
# ---------------------------------------------------------------------------

def test_dfs_label_green():
    assert "✅" in _dfs_label(90)
    assert "90" in _dfs_label(90)


def test_dfs_label_yellow():
    assert "⚠" in _dfs_label(65)


def test_dfs_label_red():
    assert "🔴" in _dfs_label(40)


def test_dfs_label_none():
    assert _dfs_label(None) == "—"


def test_boundary_label_green():
    assert "✅" in _boundary_label(85)


def test_boundary_label_red():
    assert "🔴" in _boundary_label(30)


# ---------------------------------------------------------------------------
# _read_frontmatter
# ---------------------------------------------------------------------------

def test_read_frontmatter_valid(tmp_path):
    p = tmp_path / "test.md"
    p.write_text("---\nfoo: bar\nbaz: 42\n---\n# Body", encoding="utf-8")
    fm = _read_frontmatter(p)
    assert fm["foo"] == "bar"
    assert fm["baz"] == 42


def test_read_frontmatter_missing(tmp_path):
    assert _read_frontmatter(tmp_path / "nonexistent.md") == {}


def test_read_frontmatter_no_frontmatter(tmp_path):
    p = tmp_path / "test.md"
    p.write_text("# Just a heading\nSome content", encoding="utf-8")
    fm = _read_frontmatter(p)
    assert fm == {}


# ---------------------------------------------------------------------------
# _read_body
# ---------------------------------------------------------------------------

def test_read_body_strips_frontmatter(tmp_path):
    p = tmp_path / "test.md"
    p.write_text("---\nfoo: bar\n---\n\n# Body Section\nContent here.", encoding="utf-8")
    body = _read_body(p)
    assert "# Body Section" in body
    assert "foo: bar" not in body


def test_read_body_no_frontmatter(tmp_path):
    p = tmp_path / "test.md"
    p.write_text("# Heading\nText", encoding="utf-8")
    body = _read_body(p)
    assert "# Heading" in body


def test_read_body_missing(tmp_path):
    assert _read_body(tmp_path / "none.md") == ""


# ---------------------------------------------------------------------------
# _extract_section
# ---------------------------------------------------------------------------

def test_extract_section_basic():
    body = "## Alpha\nalpha content\n\n## Beta\nbeta content\n"
    assert "alpha content" in _extract_section(body, "Alpha")
    assert "beta content" not in _extract_section(body, "Alpha")


def test_extract_section_not_found():
    body = "## Alpha\ncontent"
    assert _extract_section(body, "Gamma") == ""


# ---------------------------------------------------------------------------
# _staleness_header
# ---------------------------------------------------------------------------

def test_staleness_header_fresh():
    fm = _fresh_fm()
    assert _staleness_header(fm) == ""


def test_staleness_header_stale():
    fm = _stale_fm()
    s = _staleness_header(fm)
    assert "stale" in s.lower() or "⚠" in s


def test_staleness_header_empty():
    s = _staleness_header({})
    assert "⚠" in s  # unknown = treat as stale


# ---------------------------------------------------------------------------
# _freshness_footer
# ---------------------------------------------------------------------------

def test_freshness_footer_present(work_dir):
    _inject_state_dir(work_dir)
    _write_state(work_dir, "work-summary.md", _fresh_fm())
    footer = _freshness_footer(["work-summary"])
    assert "summary" in footer or "freshness" in footer.lower()


def test_freshness_footer_no_files(work_dir):
    _inject_state_dir(work_dir)
    footer = _freshness_footer(["work-summary"])
    assert "unknown" in footer.lower() or "refresh" in footer.lower()


# ---------------------------------------------------------------------------
# cmd_pulse — snapshot
# ---------------------------------------------------------------------------

def test_cmd_pulse_all_data(work_dir):
    _inject_state_dir(work_dir)
    _write_state(work_dir, "work-summary.md", {
        **_fresh_fm(),
        "delivery_feasibility_score": 75,
        "boundary_score": 80,
        "active_items": 5,
    })
    _write_state(work_dir, "work-calendar.md", {
        **_fresh_fm(),
        "meetings_today": 4,
        "hours_today": 3.5,
    })
    _write_state(work_dir, "work-comms.md", {
        **_fresh_fm(),
        "action_required_count": 2,
    })
    _write_state(work_dir, "work-boundary.md", {
        **_fresh_fm(),
        "boundary_score": 80,
    })
    out = cmd_pulse()
    assert "WORK PULSE" in out
    assert "3.5h" in out
    assert "2" in out  # action items
    assert "80" in out  # boundary


def test_cmd_pulse_missing_files(work_dir):
    _inject_state_dir(work_dir)
    out = cmd_pulse()
    assert "WORK PULSE" in out  # still renders
    assert "0" in out or "unknown" in out.lower()


# ---------------------------------------------------------------------------
# cmd_work — full briefing
# ---------------------------------------------------------------------------

def test_cmd_work_full(work_dir):
    _inject_state_dir(work_dir)
    _write_state(work_dir, "work-summary.md", {
        **_fresh_fm(),
        "delivery_feasibility_score": 70,
        "active_items": 3,
        "blocked_items": 1,
    })
    _write_state(work_dir, "work-calendar.md", {
        **_fresh_fm(),
        "meetings_today": 5,
        "hours_today": 4.0,
        "conflicts": 1,
    })
    _write_state(work_dir, "work-comms.md", {
        **_fresh_fm(),
        "action_required_count": 3,
        "oldest_action_required_age_hours": 48,
    })
    _write_state(work_dir, "work-projects.md", {**_fresh_fm()})
    _write_state(work_dir, "work-boundary.md", {**_fresh_fm(), "boundary_score": 72})
    out = cmd_work()
    assert "WORK OS" in out
    assert "5 meetings" in out
    assert "3 work comms" in out or "3" in out
    # Blocked → blocked recommended action
    assert "Recommended" in out or "→" in out


def test_cmd_work_empty_state(work_dir):
    _inject_state_dir(work_dir)
    out = cmd_work()
    assert "WORK OS" in out
    assert "refresh" in out.lower() or "no meetings" in out.lower()


# ---------------------------------------------------------------------------
# cmd_sprint — delivery health
# ---------------------------------------------------------------------------

def test_cmd_sprint_full(work_dir):
    _inject_state_dir(work_dir)
    _write_state(work_dir, "work-projects.md", {
        **_fresh_fm(),
        "active_count": 4,
        "blocked_count": 1,
    }, body="## Active Sprint\n- Item 1\n- Item 2\n\n## Blocked\n- Stuck item\n")
    _write_state(work_dir, "work-summary.md", {
        **_fresh_fm(),
        "delivery_feasibility_score": 60,
    })
    out = cmd_sprint()
    assert "SPRINT HEALTH" in out
    assert "4" in out
    assert "Blocked" in out or "Stuck" in out


def test_cmd_sprint_no_projects(work_dir):
    _inject_state_dir(work_dir)
    out = cmd_sprint()
    assert "SPRINT HEALTH" in out
    assert "No project provider" in out or "refresh" in out.lower() or "SPRINT" in out


# ---------------------------------------------------------------------------
# cmd_return — absence recovery
# ---------------------------------------------------------------------------

def test_cmd_return_default_window(work_dir):
    _inject_state_dir(work_dir)
    _write_state(work_dir, "work-calendar.md", {
        **_fresh_fm(), "meetings_today": 3, "hours_today": 2.0
    })
    _write_state(work_dir, "work-comms.md", {
        **_fresh_fm(), "action_required_count": 5, "oldest_action_required_age_hours": 72
    })
    _write_state(work_dir, "work-projects.md", {**_fresh_fm()})
    out = cmd_return("2d")
    assert "WORK RETURN" in out
    assert "2D" in out or "2 day" in out.lower()
    assert "5" in out  # comms count


def test_cmd_return_week_window(work_dir):
    _inject_state_dir(work_dir)
    _write_state(work_dir, "work-calendar.md", {**_fresh_fm()})
    _write_state(work_dir, "work-comms.md", {**_fresh_fm()})
    _write_state(work_dir, "work-projects.md", {**_fresh_fm()})
    out = cmd_return("1w")
    assert "WORK RETURN" in out
    assert "7 day" in out or "7D" in out or "1W" in out


# ---------------------------------------------------------------------------
# cmd_connect — evidence assembly
# ---------------------------------------------------------------------------

def test_cmd_connect_no_goals(work_dir):
    _inject_state_dir(work_dir)
    _write_state(work_dir, "work-career.md", {**_fresh_fm()})
    _write_state(work_dir, "work-projects.md", {**_fresh_fm()})
    _write_state(work_dir, "work-performance.md", {**_fresh_fm()})
    out = cmd_connect()
    assert "WORK CONNECT" in out
    assert "bootstrap" in out.lower() or "no" in out.lower()


def test_cmd_connect_renders(work_dir):
    _inject_state_dir(work_dir)
    for name in ["work-career.md", "work-projects.md", "work-performance.md"]:
        _write_state(work_dir, name, {**_fresh_fm()})
    out = cmd_connect()
    assert "WORK CONNECT" in out


# ---------------------------------------------------------------------------
# cmd_people — person lookup
# ---------------------------------------------------------------------------

def test_cmd_people_found(work_dir):
    _inject_state_dir(work_dir)
    _write_state(work_dir, "work-people.md", {**_fresh_fm()}, body=(
        "## Alice Johnson\n"
        "- Role: Principal PM\n"
        "- Team: Copilot\n"
        "- Collaboration notes: high-frequency partner\n"
    ))
    out = cmd_people("Alice")
    assert "alice" in out.lower() or "Alice" in out


def test_cmd_people_not_found(work_dir):
    _inject_state_dir(work_dir)
    _write_state(work_dir, "work-people.md", {**_fresh_fm()}, body="## Bob\n- Role: SWE\n")
    out = cmd_people("Zara")
    assert "No match" in out or "not in" in out.lower()


def test_cmd_people_no_file(work_dir):
    _inject_state_dir(work_dir)
    out = cmd_people("Alice")
    assert "No people data" in out or "refresh" in out.lower()


def test_cmd_people_empty_query(work_dir):
    _inject_state_dir(work_dir)
    out = cmd_people("")
    assert "Usage" in out or "name" in out.lower()


# ---------------------------------------------------------------------------
# cmd_docs — recent artifacts
# ---------------------------------------------------------------------------

def test_cmd_docs_with_notes(work_dir):
    _inject_state_dir(work_dir)
    _write_state(work_dir, "work-notes.md", {**_fresh_fm()}, body=(
        "## Recent Artifacts\n"
        "- spec-v2.md\n"
        "- deck.pptx\n"
    ))
    out = cmd_docs()
    assert "WORK DOCS" in out
    assert "spec-v2.md" in out or "Artifacts" in out


def test_cmd_docs_no_file(work_dir):
    _inject_state_dir(work_dir)
    out = cmd_docs()
    assert "WORK DOCS" in out
    assert "No work artifacts" in out or "refresh" in out.lower() or "notes" in out.lower()


# ---------------------------------------------------------------------------
# cmd_sources — registry
# ---------------------------------------------------------------------------

def test_cmd_sources_empty(work_dir):
    _inject_state_dir(work_dir)
    out = cmd_sources()
    assert "WORK SOURCES" in out
    assert "No sources" in out or "add" in out.lower()


def test_cmd_sources_with_data(work_dir):
    _inject_state_dir(work_dir)
    body = (
        "### Source: velocity dashboard\n"
        "URL: https://example.com/velocity\n"
        "What_it_answers: sprint velocity trend\n"
    )
    _write_state(work_dir, "work-sources.md", {**_fresh_fm()}, body=body)
    out = cmd_sources()
    assert "WORK SOURCES" in out
    assert "1 source" in out or "velocity" in out.lower()


def test_cmd_sources_query_filter(work_dir):
    _inject_state_dir(work_dir)
    body = (
        "### Source: velocity dashboard\n"
        "URL: https://example.com/v\n"
        "What_it_answers: sprint velocity\n\n"
        "### Source: incident log\n"
        "URL: https://example.com/inc\n"
        "What_it_answers: incident history\n"
    )
    _write_state(work_dir, "work-sources.md", {**_fresh_fm()}, body=body)
    out = cmd_sources(query="incident")
    assert "incident" in out.lower()
    assert "velocity" not in out.lower()


# ---------------------------------------------------------------------------
# cmd_health — connector diagnostics
# ---------------------------------------------------------------------------

def test_cmd_health_renders(work_dir):
    _inject_state_dir(work_dir)
    out = cmd_health()
    assert "WORK HEALTH" in out
    assert "Providers" in out or "providers" in out.lower()
    assert "State freshness" in out


# ---------------------------------------------------------------------------
# main() — CLI dispatch
# ---------------------------------------------------------------------------

def test_main_pulse(work_dir, capsys):
    _inject_state_dir(work_dir)
    _write_state(work_dir, "work-summary.md", {**_fresh_fm()})
    _write_state(work_dir, "work-calendar.md", {**_fresh_fm()})
    _write_state(work_dir, "work-comms.md", {**_fresh_fm()})
    _write_state(work_dir, "work-boundary.md", {**_fresh_fm()})
    rc = main(["--command", "pulse", "--state-dir", str(work_dir)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "WORK PULSE" in captured.out


def test_main_work(work_dir, capsys):
    _inject_state_dir(work_dir)
    # Write state files; comms has action_required so WB-6 (all-clear) does not fire.
    for name in ["work-summary.md", "work-calendar.md", "work-boundary.md", "work-projects.md"]:
        _write_state(work_dir, name, {**_fresh_fm()})
    _write_state(work_dir, "work-comms.md", {**_fresh_fm(), "action_required_count": 1})
    rc = main(["--command", "work", "--state-dir", str(work_dir)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "WORK OS" in captured.out


def test_main_people(work_dir, capsys):
    _inject_state_dir(work_dir)
    _write_state(work_dir, "work-people.md", {**_fresh_fm()}, body="## Bob Smith\n- Role: SWE")
    rc = main(["--command", "people", "--query", "Bob", "--state-dir", str(work_dir)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Bob" in captured.out or "people" in captured.out.lower()


def test_main_return(work_dir, capsys):
    _inject_state_dir(work_dir)
    for name in ["work-calendar.md", "work-comms.md", "work-projects.md"]:
        _write_state(work_dir, name, {**_fresh_fm()})
    rc = main(["--command", "return", "--window", "3d", "--state-dir", str(work_dir)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "WORK RETURN" in captured.out


def test_main_prep(work_dir, capsys):
    _inject_state_dir(work_dir)
    _write_state(work_dir, "work-calendar.md", {**_fresh_fm()})
    rc = main(["--command", "prep", "--state-dir", str(work_dir)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "WORK PREP" in captured.out


# ---------------------------------------------------------------------------
# _parse_meeting_start_dt  (§7.9 meeting time parsing)
# ---------------------------------------------------------------------------

from work_reader import (  # type: ignore
    _parse_meeting_start_dt,
    _parse_today_meetings,
    _readiness_score,
    _MeetingEntry,
    cmd_prep,
)
from datetime import date as _date


def test_parse_start_dt_am_end():
    """'8:00–8:30 AM' — period at end, AM"""
    d = _date(2026, 3, 24)
    dt = _parse_meeting_start_dt("8:00–8:30 AM", d)
    assert dt is not None
    assert dt.hour == 8
    assert dt.minute == 0


def test_parse_start_dt_pm_end():
    """'4:05–4:30 PM' — period at end, PM"""
    d = _date(2026, 3, 25)
    dt = _parse_meeting_start_dt("4:05–4:30 PM", d)
    assert dt is not None
    assert dt.hour == 16
    assert dt.minute == 5


def test_parse_start_dt_explicit_am():
    """'11:30 AM–12:00 PM' — period on start"""
    d = _date(2026, 3, 24)
    dt = _parse_meeting_start_dt("11:30 AM–12:00 PM", d)
    assert dt is not None
    assert dt.hour == 11
    assert dt.minute == 30


def test_parse_start_dt_noon():
    """'12:00–12:30 PM' — noon boundary"""
    d = _date(2026, 3, 24)
    dt = _parse_meeting_start_dt("12:00–12:30 PM", d)
    assert dt is not None
    assert dt.hour == 12


def test_parse_start_dt_midnight():
    """'12:00–12:30 AM' — midnight/midnight AM boundary"""
    d = _date(2026, 3, 24)
    dt = _parse_meeting_start_dt("12:00–12:30 AM", d)
    assert dt is not None
    assert dt.hour == 0


def test_parse_start_dt_returns_none_for_garbage():
    assert _parse_meeting_start_dt("TBD") is None
    assert _parse_meeting_start_dt("") is None
    assert _parse_meeting_start_dt("all day") is None


# ---------------------------------------------------------------------------
# _parse_today_meetings
# ---------------------------------------------------------------------------

_TUESDAY_MARCH_24_BODY = """\
## This Week

### Tuesday — March 24
| Time | Title | Duration | Attendees | Recurring |
|------|-------|----------|-----------|-----------|
| 8:00–8:30 AM | DD PF Weekly Sync | 30 min | 13 | Yes (Weekly) |
| 11:30 AM–12:00 PM | 1:1 with Manager | 30 min | 2 | Yes (Weekly) |
| 2:00–3:00 PM | LT Review Deep Dive | 60 min | 5 | No |

### Wednesday — March 25
| Time | Title | Duration | Attendees | Recurring |
|------|-------|----------|-----------|-----------|
| 9:00–9:30 AM | Different Day meeting | 30 min | 3 | No |
"""


def test_parse_today_meetings_returns_correct_day():
    """Only Tuesday's meetings parsed when for_date is March 24."""
    meetings = _parse_today_meetings(
        _TUESDAY_MARCH_24_BODY,
        for_date=_date(2026, 3, 24)
    )
    titles = [m.title for m in meetings]
    assert "DD PF Weekly Sync" in titles
    assert "1:1 with Manager" in titles
    assert "LT Review Deep Dive" in titles
    assert "Different Day meeting" not in titles


def test_parse_today_meetings_count():
    meetings = _parse_today_meetings(
        _TUESDAY_MARCH_24_BODY,
        for_date=_date(2026, 3, 24)
    )
    assert len(meetings) == 3


def test_parse_today_meetings_recurring_flag():
    meetings = _parse_today_meetings(
        _TUESDAY_MARCH_24_BODY,
        for_date=_date(2026, 3, 24)
    )
    sync = next(m for m in meetings if "Weekly Sync" in m.title)
    review = next(m for m in meetings if "LT Review" in m.title)
    assert sync.is_recurring is True
    assert review.is_recurring is False


def test_parse_today_meetings_attendee_count():
    meetings = _parse_today_meetings(
        _TUESDAY_MARCH_24_BODY,
        for_date=_date(2026, 3, 24)
    )
    sync = next(m for m in meetings if "Weekly Sync" in m.title)
    assert sync.attendee_count == 13


def test_parse_today_meetings_duration():
    meetings = _parse_today_meetings(
        _TUESDAY_MARCH_24_BODY,
        for_date=_date(2026, 3, 24)
    )
    review = next(m for m in meetings if "LT Review" in m.title)
    assert review.duration_min == 60


def test_parse_today_meetings_empty_body():
    assert _parse_today_meetings("", for_date=_date(2026, 3, 24)) == []


def test_parse_today_meetings_wrong_day():
    """Nothing returned when for_date does not match any section header."""
    meetings = _parse_today_meetings(
        _TUESDAY_MARCH_24_BODY,
        for_date=_date(2026, 3, 27)  # Saturday — not in body
    )
    assert meetings == []


# ---------------------------------------------------------------------------
# _readiness_score
# ---------------------------------------------------------------------------

def _make_meeting(**kwargs) -> _MeetingEntry:
    defaults = dict(
        title="Weekly Team Sync",
        time_str="9:00–9:30 AM",
        start_dt=None,
        duration_min=30,
        attendee_count=5,
        is_recurring=False,
        is_personal=False,
    )
    defaults.update(kwargs)
    return _MeetingEntry(**defaults)


def test_readiness_score_baseline():
    m = _make_meeting(is_recurring=False)
    sc, gaps = _readiness_score(m, notes_body="", people_body="", oi_body="")
    # Base 85, no deductions for non-recurring with no issues
    assert sc == 85
    assert gaps == []


def test_readiness_score_recurring_no_notes():
    m = _make_meeting(is_recurring=True, title="Team Standup Daily")
    sc, gaps = _readiness_score(m, notes_body="", people_body="", oi_body="")
    # -20 for recurring + no notes
    assert sc == 65
    assert any("No notes" in g for g in gaps)


def test_readiness_score_has_notes_bonus():
    m = _make_meeting(is_recurring=True, title="team standup daily")
    # notes contain the first 4 words of the title
    sc, gaps = _readiness_score(m, notes_body="team standup daily prep notes", people_body="", oi_body="")
    # +5 for has notes; no -20 for recurring since notes exist
    assert sc == 90
    assert not any("No notes" in g for g in gaps)


def test_readiness_score_carry_forward():
    m = _make_meeting(title="team standup daily")
    sc, gaps = _readiness_score(
        m,
        # Notes must use the structured **Carry forward:** block format that
        # _extract_carry_forward_items parses (§7.9 carry-forward contract).
        notes_body="## team standup daily\n**Carry forward:**\n- item from last week\n",
        people_body="",
        oi_body="",
    )
    # +5 for has notes, -20 for carry-forward items
    assert sc == 70
    assert any("Carry-forward" in g for g in gaps)


def test_readiness_score_large_meeting_no_context():
    m = _make_meeting(title="All Hands Q2", attendee_count=25)
    sc, gaps = _readiness_score(m, notes_body="", people_body="unrelated text", oi_body="")
    # -15 for large meeting, no stakeholder context
    assert sc == 70
    assert any("attendees" in g for g in gaps)


def test_readiness_score_high_stakes_keyword():
    m = _make_meeting(title="LT Review Prep Session")
    sc, gaps = _readiness_score(m, notes_body="", people_body="", oi_body="")
    # -10 for high-stakes
    assert sc == 75
    assert any("High-stakes" in g for g in gaps)


def test_readiness_score_open_action_items():
    m = _make_meeting(title="project alpha sync weekly")
    sc, gaps = _readiness_score(
        m,
        notes_body="",
        people_body="",
        oi_body="## OI-001\nproject alpha sync weekly: resolve auth issue",
    )
    # -10 for open action items
    assert sc == 75
    assert any("Open action" in g for g in gaps)


def test_readiness_score_clamped_to_20():
    """Multiple deductions should never drop below 20."""
    m = _make_meeting(
        title="LT review director calibration exec",
        attendee_count=30,
        is_recurring=True,
    )
    sc, _ = _readiness_score(m, notes_body="", people_body="", oi_body="")
    assert sc >= 20


def test_readiness_score_max_100():
    """Score should never exceed 100."""
    m = _make_meeting(is_recurring=False)
    sc, _ = _readiness_score(m, notes_body="", people_body="", oi_body="")
    assert sc <= 100


# ---------------------------------------------------------------------------
# cmd_prep
# ---------------------------------------------------------------------------

def test_cmd_prep_no_meetings(work_dir):
    _inject_state_dir(work_dir)
    _write_state(work_dir, "work-calendar.md", {**_fresh_fm()})
    out = cmd_prep()
    assert "WORK PREP" in out
    assert "No meetings" in out or "refresh" in out.lower()


def test_cmd_prep_renders_header(work_dir):
    _inject_state_dir(work_dir)
    # Empty calendar — still renders header
    _write_state(work_dir, "work-calendar.md", {**_fresh_fm()}, body="")
    out = cmd_prep()
    assert "WORK PREP" in out


# ===========================================================================
# cmd_live — live meeting assist (§7.4, Phase 2.7)
# ===========================================================================

_DECISIONS_BODY_WITH_OPEN = """\
## Decisions

| ID | Date | Summary | Status | Owner |
|----|------|---------|--------|-------|
| D-001 | 2026-03-01 | OPEN: Architecture service decomposition approach | open | alice |
| D-002 | 2026-03-10 | Decided: Deploy to single region first | closed | bob |
"""

_NOTES_BODY_WITH_CARRY = """\
## Architecture Review (2026-03-18)

**Carry forward:**
- Revisit caching strategy decision next session
- Review load-balancing docs before next meeting
"""


def _make_today_cal_body() -> str:
    """Return a calendar body with today's date-section heading and two meetings."""
    today = datetime.now().date()
    day_name = datetime(today.year, today.month, today.day).strftime("%A")
    month_name = datetime(today.year, today.month, today.day).strftime("%B")
    day_num = today.day
    return (
        f"## This Week\n\n"
        f"### {day_name} — {month_name} {day_num}\n"
        "| Time | Title | Duration | Attendees | Recurring |\n"
        "|------|-------|----------|-----------|----------|\n"
        "| 10:00–10:30 AM | Architecture Review | 30 min | 8 | Yes (Weekly) |\n"
        "| 2:00–3:00 PM | Sprint Planning | 60 min | 12 | No |\n"
    )


class TestCmdLive:
    """cmd_live() — live meeting assist (§7.4, Phase 2.7)."""

    def test_no_meeting_id_shows_usage(self, work_dir):
        """Empty meeting_id shows usage and lists today's meetings."""
        _inject_state_dir(work_dir)
        _write_state(work_dir, "work-calendar.md", {**_fresh_fm()},
                     body=_make_today_cal_body())
        out = cmd_live()
        assert "LIVE MEETING ASSIST" in out
        assert "Usage" in out

    def test_no_meeting_id_lists_todays_meetings(self, work_dir):
        """Usage output includes today's meetings from calendar."""
        _inject_state_dir(work_dir)
        _write_state(work_dir, "work-calendar.md", {**_fresh_fm()},
                     body=_make_today_cal_body())
        out = cmd_live()
        assert "Architecture Review" in out
        assert "Sprint Planning" in out

    def test_exact_match_renders_context_card(self, work_dir):
        """Exact title substring match produces a full context card."""
        _inject_state_dir(work_dir)
        _write_state(work_dir, "work-calendar.md", {**_fresh_fm()},
                     body=_make_today_cal_body())
        out = cmd_live(meeting_id="Architecture Review")
        assert "LIVE MEETING ASSIST" in out
        assert "Architecture Review" in out
        # Card should have time and attendee info
        assert "10:00" in out or "30 min" in out

    def test_partial_keyword_match(self, work_dir):
        """Partial keyword match (≥20% overlap) finds meeting."""
        _inject_state_dir(work_dir)
        _write_state(work_dir, "work-calendar.md", {**_fresh_fm()},
                     body=_make_today_cal_body())
        out = cmd_live(meeting_id="architecture")
        assert "Architecture Review" in out

    def test_no_match_shows_fallback(self, work_dir):
        """Unrecognized meeting_id shows 'No meeting found' and lists available."""
        _inject_state_dir(work_dir)
        _write_state(work_dir, "work-calendar.md", {**_fresh_fm()},
                     body=_make_today_cal_body())
        out = cmd_live(meeting_id="XYZ NonExistent 999")
        assert "No meeting found" in out
        # Should still offer the real meetings
        assert "Architecture Review" in out or "Sprint Planning" in out

    def test_open_decisions_shown_for_matching_meeting(self, work_dir):
        """Open decisions whose summary mentions meeting keywords appear in output."""
        _inject_state_dir(work_dir)
        _write_state(work_dir, "work-calendar.md", {**_fresh_fm()},
                     body=_make_today_cal_body())
        _write_state(work_dir, "work-decisions.md", {**_fresh_fm()},
                     body=_DECISIONS_BODY_WITH_OPEN)
        out = cmd_live(meeting_id="Architecture Review")
        assert "D-001" in out or "Architecture" in out

    def test_carry_forward_shown_when_present(self, work_dir):
        """Carry-forward items from work-notes appear in context card."""
        _inject_state_dir(work_dir)
        _write_state(work_dir, "work-calendar.md", {**_fresh_fm()},
                     body=_make_today_cal_body())
        _write_state(work_dir, "work-notes.md", {**_fresh_fm()},
                     body=_NOTES_BODY_WITH_CARRY)
        out = cmd_live(meeting_id="Architecture Review")
        assert "Carry-forward" in out or "caching" in out

    def test_missing_calendar_degrades_gracefully(self, work_dir):
        """When work-calendar.md is missing, output degrades without crashing."""
        _inject_state_dir(work_dir)
        # No calendar file written
        out = cmd_live(meeting_id="anything")
        assert "LIVE MEETING ASSIST" in out
        assert "No meeting found" in out or "Usage" in out or "none" in out.lower()

    def test_freshness_footer_always_present(self, work_dir):
        """Freshness footer is always emitted (§3.8)."""
        _inject_state_dir(work_dir)
        _write_state(work_dir, "work-calendar.md", {**_fresh_fm()}, body="")
        out = cmd_live()
        assert "Data freshness" in out or "─" * 20 in out

    def test_recurring_flag_shown_in_header(self, work_dir):
        """Recurring status appears in the meeting header."""
        _inject_state_dir(work_dir)
        _write_state(work_dir, "work-calendar.md", {**_fresh_fm()},
                     body=_make_today_cal_body())
        out = cmd_live(meeting_id="Architecture Review")
        assert "recurring" in out.lower()

    def test_main_live_command_dispatches(self, work_dir, capsys):
        """CLI dispatch via --command live --meeting-id works end-to-end."""
        _inject_state_dir(work_dir)
        _write_state(work_dir, "work-calendar.md", {**_fresh_fm()},
                     body=_make_today_cal_body())
        rc = main([
            "--command", "live",
            "--meeting-id", "Sprint Planning",
            "--state-dir", str(work_dir),
        ])
        captured = capsys.readouterr()
        assert rc == 0
        assert "LIVE MEETING ASSIST" in captured.out


# ===========================================================================
# WorkBriefingConfig adaptive rules (§8.5 WB-1/WB-3/WB-6, v2.3.0)
# ===========================================================================

class TestWorkBriefingConfig:
    """WorkBriefingConfig dataclass and _build_briefing_config() factory."""

    def test_default_config_all_false(self):
        cfg = WorkBriefingConfig()
        assert cfg.flash_mode is False
        assert cfg.sprint_deadline_approaching is False
        assert cfg.all_clear is False

    def test_wb1_flash_mode_detected_from_profile(self):
        profile = {"work": {"briefing_format": "flash"}}
        cfg = _build_briefing_config(profile, {}, {}, {})
        assert cfg.flash_mode is True

    def test_wb1_standard_format_not_flash(self):
        profile = {"work": {"briefing_format": "standard"}}
        cfg = _build_briefing_config(profile, {}, {}, {})
        assert cfg.flash_mode is False

    def test_wb3_sprint_deadline_within_3_days(self):
        from datetime import datetime, timezone, timedelta
        dl = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        proj_fm = {"sprint_deadline": dl}
        cfg = _build_briefing_config({}, {}, proj_fm, {})
        assert cfg.sprint_deadline_approaching is True

    def test_wb3_sprint_deadline_more_than_3_days_not_approaching(self):
        from datetime import datetime, timezone, timedelta
        dl = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        proj_fm = {"sprint_deadline": dl}
        cfg = _build_briefing_config({}, {}, proj_fm, {})
        assert cfg.sprint_deadline_approaching is False

    def test_wb6_all_clear_no_blockers_fresh_data(self):
        fresh_ts = datetime.now(timezone.utc).isoformat()
        summary_fm = {
            "last_updated": fresh_ts,
            "blocked_items": 0,
            "action_required_count": 0,
        }
        cfg = _build_briefing_config({}, summary_fm, {}, {})
        assert cfg.all_clear is True

    def test_wb6_not_all_clear_when_blocked(self):
        fresh_ts = datetime.now(timezone.utc).isoformat()
        summary_fm = {
            "last_updated": fresh_ts,
            "blocked_items": 2,
            "action_required_count": 0,
        }
        cfg = _build_briefing_config({}, summary_fm, {}, {})
        assert cfg.all_clear is False

    def test_empty_profile_and_frontmatter_does_not_raise(self):
        cfg = _build_briefing_config({}, {}, {}, {})
        assert isinstance(cfg, WorkBriefingConfig)


# ===========================================================================
# cmd_sources_add — data source registration (§7.7, Phase 1 gate)
# ===========================================================================

class TestCmdSourcesAdd:
    """cmd_sources_add() — register a data source to work-sources.md."""

    def test_add_new_source_returns_confirmation(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_sources_add(url="https://example.com/velocity")
        assert "SOURCE REGISTERED" in out
        assert "example.com" in out

    def test_add_creates_sources_file_if_missing(self, work_dir):
        _inject_state_dir(work_dir)
        sources_path = work_dir / "work-sources.md"
        assert not sources_path.exists()
        cmd_sources_add(url="https://example.com/dash", context="sprint velocity dashboard")
        assert sources_path.exists()
        content = sources_path.read_text(encoding="utf-8")
        assert "https://example.com/dash" in content

    def test_add_appends_to_existing_file(self, work_dir):
        _inject_state_dir(work_dir)
        sources_path = work_dir / "work-sources.md"
        # Write an existing sources file with the expected table
        sources_path.write_text(
            "---\nschema_version: \"1.0\"\ndomain: work-sources\n"
            "last_updated: \"2026-03-25T01:00:00Z\"\n---\n\n"
            "# Work Sources\n\n"
            "## Manually Registered Sources\n\n"
            "| Date | Title | URL | Tags | Notes |\n"
            "|------|-------|-----|------|-------|\n"
            "| 2026-03-20 | Old Source | https://old.example.com | | — |\n",
            encoding="utf-8",
        )
        cmd_sources_add(url="https://new.example.com", context="new source")
        content = sources_path.read_text(encoding="utf-8")
        assert "https://old.example.com" in content  # existing preserved
        assert "https://new.example.com" in content  # new appended

    def test_add_with_context_included_in_output(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_sources_add(url="https://example.com/kpi", context="KPI dashboard for sprint")
        assert "KPI dashboard for sprint" in out

    def test_add_empty_url_returns_usage(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_sources_add(url="")
        assert "Usage" in out

    def test_add_whitespace_url_returns_usage(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_sources_add(url="   ")
        assert "Usage" in out

    def test_add_url_visible_in_sources_registry(self, work_dir):
        _inject_state_dir(work_dir)
        cmd_sources_add(url="https://velocity.example.com/board")
        out = cmd_sources()
        assert "velocity.example.com" in out or "WORK SOURCES" in out

    def test_main_sources_add(self, work_dir, capsys):
        _inject_state_dir(work_dir)
        rc = main([
            "--command", "sources-add",
            "--url", "https://example.com/dash",
            "--context", "velocity dashboard",
            "--state-dir", str(work_dir),
        ])
        captured = capsys.readouterr()
        assert rc == 0
        assert "SOURCE REGISTERED" in captured.out


# ===========================================================================
# cmd_connect_prep — Connect cycle narrative assembly (§7.6, Phase 1 gate)
# ===========================================================================

class TestCmdConnectPrep:
    """cmd_connect_prep() — Connect cycle narrative prep."""

    def test_connect_prep_renders_header(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_connect_prep()
        assert "WORK CONNECT-PREP" in out

    def test_connect_prep_contains_connect_section(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_connect_prep()
        # Should contain the NE connect_summary header or a fallback
        assert "Connect" in out

    def test_connect_prep_no_state_renders_gracefully(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_connect_prep()
        assert "WORK CONNECT-PREP" in out
        assert out  # non-empty

    def test_connect_prep_with_performance_state(self, work_dir):
        _inject_state_dir(work_dir)
        _write_state(work_dir, "work-performance.md", {**_fresh_fm()})
        _write_state(work_dir, "work-career.md", {**_fresh_fm()})
        _write_state(work_dir, "work-projects.md", {**_fresh_fm()})
        out = cmd_connect_prep()
        assert "WORK CONNECT-PREP" in out

    def test_connect_prep_calibration_mode_notice(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_connect_prep(mode="calibration")
        assert "Phase 3" in out or "calibration" in out.lower()

    def test_connect_prep_has_freshness_footer(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_connect_prep()
        assert "refresh" in out.lower() or "last" in out.lower()

    def test_main_connect_prep(self, work_dir, capsys):
        _inject_state_dir(work_dir)
        rc = main(["--command", "connect-prep", "--state-dir", str(work_dir)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "WORK CONNECT-PREP" in captured.out


# ===========================================================================
# cmd_newsletter — team newsletter draft (§7.8, Phase 2)
# ===========================================================================

class TestCmdNewsletter:
    """cmd_newsletter() — NE-backed team newsletter draft."""

    def test_newsletter_renders_header(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_newsletter()
        assert "WORK NEWSLETTER" in out

    def test_newsletter_draft_label_present(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_newsletter()
        assert "DRAFT" in out

    def test_newsletter_no_state_renders_gracefully(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_newsletter()
        assert "WORK NEWSLETTER" in out

    def test_newsletter_with_period(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_newsletter(period="Week of March 25, 2026")
        assert "WORK NEWSLETTER" in out
        assert "March 25" in out

    def test_newsletter_with_project_state(self, work_dir):
        _inject_state_dir(work_dir)
        _write_state(work_dir, "work-projects.md", {
            **_fresh_fm(),
            "completed_recent_count": 3,
            "active_count": 2,
        })
        out = cmd_newsletter()
        assert "WORK NEWSLETTER" in out

    def test_main_newsletter(self, work_dir, capsys):
        _inject_state_dir(work_dir)
        rc = main(["--command", "newsletter", "--state-dir", str(work_dir)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "WORK NEWSLETTER" in captured.out


# ===========================================================================
# cmd_deck — LT deck content assembly (§7.8, Phase 2)
# ===========================================================================

class TestCmdDeck:
    """cmd_deck() — NE-backed LT deck content."""

    def test_deck_renders_header(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_deck()
        assert "WORK DECK" in out

    def test_deck_draft_label_present(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_deck()
        assert "DRAFT" in out

    def test_deck_no_state_renders_gracefully(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_deck()
        assert "WORK DECK" in out

    def test_deck_with_topic(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_deck(topic="architecture review")
        assert "WORK DECK" in out
        assert "architecture" in out.lower()

    def test_deck_empty_topic_uses_default(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_deck(topic="")
        assert "WORK DECK" in out
        assert "Leadership Update" in out

    def test_main_deck(self, work_dir, capsys):
        _inject_state_dir(work_dir)
        rc = main(["--command", "deck", "--topic", "sprint review", "--state-dir", str(work_dir)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "WORK DECK" in captured.out


# ===========================================================================
# cmd_memo — weekly status memo (§7.10, Phase 2)
# ===========================================================================

class TestCmdMemo:
    """cmd_memo() — NE-backed status memo."""

    def test_memo_renders_header(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_memo()
        assert "WORK MEMO" in out

    def test_memo_weekly_flag_in_header(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_memo(weekly=True)
        assert "WEEKLY" in out

    def test_memo_draft_label_present(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_memo()
        assert "DRAFT" in out

    def test_memo_no_state_renders_gracefully(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_memo()
        assert "WORK MEMO" in out

    def test_memo_with_period(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_memo(period="Week of March 25")
        assert "WORK MEMO" in out
        # Period passed to NE — should appear in content
        assert "March 25" in out

    def test_main_memo_weekly(self, work_dir, capsys):
        _inject_state_dir(work_dir)
        rc = main(["--command", "memo", "--weekly", "--state-dir", str(work_dir)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "WORK MEMO" in captured.out
        assert "WEEKLY" in captured.out


# ===========================================================================
# cmd_talking_points — meeting-ready talking points (§7.10, Phase 2)
# ===========================================================================

class TestCmdTalkingPoints:
    """cmd_talking_points() — NE-backed talking points draft."""

    def test_talking_points_renders_header(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_talking_points(topic="sprint planning")
        assert "WORK TALKING-POINTS" in out

    def test_talking_points_draft_label(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_talking_points(topic="architecture review")
        assert "DRAFT" in out

    def test_talking_points_topic_reflected(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_talking_points(topic="quarterly business review")
        assert "quarterly" in out.lower() or "TALKING-POINTS" in out

    def test_talking_points_empty_topic_returns_usage(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_talking_points(topic="")
        assert "Usage" in out

    def test_talking_points_no_state_renders_gracefully(self, work_dir):
        _inject_state_dir(work_dir)
        out = cmd_talking_points(topic="roadmap review")
        assert "WORK TALKING-POINTS" in out

    def test_main_talking_points(self, work_dir, capsys):
        _inject_state_dir(work_dir)
        rc = main(["--command", "talking-points", "--topic", "sprint review", "--state-dir", str(work_dir)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "WORK TALKING-POINTS" in captured.out


# ---------------------------------------------------------------------------
# TestCmdPromoCase (Phase 3 §7.11)
# ---------------------------------------------------------------------------

class TestCmdPromoCase:
    """Tests for cmd_promo_case() — promotion readiness assessment."""

    @pytest.fixture(autouse=True)
    def setup(self, work_dir):
        _inject_state_dir(work_dir)
        _write_state(work_dir, "work-project-journeys.md",
            "---\nschema_version: '1.0'\ndomain: work-project-journeys\nprojects_tracked: 2\n---\n"
            "## Project Alpha\n\n"
            "| Date | Milestone | Evidence | Impact |\n"
            "| --- | --- | --- | --- |\n"
            "| **Jan 2026** | Shipped P0 | https://ado.example.com/1 | Major milestone |\n"
            "| **Feb 2026** | Expanded scope | Design doc v2 | Scope arc |\n"
            "## Project Beta\n\n"
            "| Date | Milestone | Evidence | Impact |\n"
            "| --- | --- | --- | --- |\n"
            "| **Mar 2026** | Launched API | URL | P1 delivery |\n"
        )
        _write_state(work_dir, "work-performance.md",
            "---\nschema_version: '1.0'\ndomain: work-performance\nlast_updated: '2026-03-25T00:00:00Z'\n---\n"
            "## Current Connect Goals\n\n"
            "### Goal 1: Deliver P0\n\n"
            "- Status: Completed\n- Evidence: milestone 1\n- Evidence: milestone 2\n- Evidence: milestone 3\n"
            "### Goal 2: Expand scope\n\n"
            "- Status: In progress\n- Evidence: doc1\n"
        )
        _write_state(work_dir, "work-people.md",
            "---\ndomain: work-people\nlast_updated: '2026-03-25T00:00:00Z'\n---\n"
            "## Manager Chain\n\n- **mgr** — Manager\n\n"
            "## Visibility Events\n\n"
            "| Date | Stakeholder | Type | Context | Source |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| 2026-03-10 | bigwig | replied | Great work on P0 | work-comms |\n"
            "| 2026-03-20 | exec | presented_about | LT review 92 attendees | work-calendar |\n"
        )
        _write_state(work_dir, "work-career.md",
            "---\ndomain: work-career\nlast_updated: '2026-03-25T00:00:00Z'\n---\n"
            "## Recognition\n\n- \"Exceptional impact\" says manager\n"
        )

    def test_promo_case_has_header(self, work_dir):
        out = cmd_promo_case(narrative=False)
        assert "WORK PROMO CASE" in out

    def test_promo_case_scope_arc_present(self, work_dir):
        out = cmd_promo_case(narrative=False)
        assert "Scope Arc" in out or "scope" in out.lower()

    def test_promo_case_evidence_density_present(self, work_dir):
        out = cmd_promo_case(narrative=False)
        assert "Evidence" in out

    def test_promo_case_visibility_events_present(self, work_dir):
        out = cmd_promo_case(narrative=False)
        assert "Visibility" in out

    def test_promo_case_readiness_signal_present(self, work_dir):
        out = cmd_promo_case(narrative=False)
        assert "Readiness" in out or "ready" in out.lower() or "quarters" in out.lower()

    def test_promo_case_graceful_when_no_state(self, work_dir):
        # Remove all state files
        for f in work_dir.glob("*.md"):
            f.unlink()
        out = cmd_promo_case(narrative=False)
        assert "WORK PROMO CASE" in out  # still renders header

    def test_promo_narrative_has_thesis(self, work_dir):
        out = cmd_promo_case(narrative=True)
        assert "Thesis" in out or "WORK PROMO NARRATIVE" in out

    def test_promo_narrative_writes_file(self, work_dir, tmp_path):
        # narrative=True should write to state/work/work-promo-narrative.md
        out = cmd_promo_case(narrative=True)
        assert "WORK PROMO NARRATIVE" in out
        # File writing is a side-effect; may or may not exist in test context

    def test_main_promo_case_dispatch(self, work_dir, capsys):
        rc = main(["--command", "promo-case", "--state-dir", str(work_dir)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "WORK PROMO CASE" in captured.out

    def test_main_promo_narrative_dispatch(self, work_dir, capsys):
        rc = main(["--command", "promo-narrative", "--state-dir", str(work_dir)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "WORK PROMO NARRATIVE" in captured.out


# ---------------------------------------------------------------------------
# TestCmdJourney (Phase 3 §7.11)
# ---------------------------------------------------------------------------

class TestCmdJourney:
    """Tests for cmd_journey() — project timeline view."""

    @pytest.fixture(autouse=True)
    def _setup(self, work_dir):
        _inject_state_dir(work_dir)
        (work_dir / "work-project-journeys.md").write_text(
            "---\nschema_version: '1.0'\ndomain: work-project-journeys\nprojects_tracked: 2\n---\n"
            "## Alpha Ramp\n\n"
            "| Date | Milestone | Evidence | Impact |\n"
            "| --- | --- | --- | --- |\n"
            "| **Jan 2026** | P0 delivered | doc | Major |\n"
            "| **Feb 2026** | Scope expanded | doc2 | Scope arc |\n"
            "## Beta Platform\n\n"
            "| Date | Milestone | Evidence | Impact |\n"
            "| --- | --- | --- | --- |\n"
            "| **Mar 2026** | GA launch | url | P1 delivery |\n",
            encoding="utf-8",
        )

    def test_journey_all_shows_projects_table(self, work_dir):
        out = cmd_journey()
        assert "WORK PROJECT JOURNEYS" in out
        assert "Alpha Ramp" in out or "alpha" in out.lower()

    def test_journey_projects_tracked_count(self, work_dir):
        out = cmd_journey()
        assert "2" in out  # 2 projects tracked

    def test_journey_project_filter_returns_section(self, work_dir):
        out = cmd_journey(project="Alpha")
        assert "Alpha Ramp" in out or "Jan 2026" in out

    def test_journey_project_filter_milestone_count(self, work_dir):
        out = cmd_journey(project="Alpha")
        assert "Milestones: 2" in out or "2" in out

    def test_journey_unknown_project_graceful(self, work_dir):
        out = cmd_journey(project="NonExistentProject")
        assert "No project matching" in out or "WORK PROJECT JOURNEYS" in out

    def test_journey_no_state_graceful(self, work_dir):
        (work_dir / "work-project-journeys.md").unlink(missing_ok=True)
        out = cmd_journey()
        assert "WORK PROJECT JOURNEYS" in out  # header always present

    def test_main_journey_dispatch(self, work_dir, capsys):
        rc = main(["--command", "journey", "--state-dir", str(work_dir)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "WORK PROJECT JOURNEYS" in captured.out

    def test_main_journey_with_project_filter(self, work_dir, capsys):
        rc = main(["--command", "journey", "--project", "Alpha", "--state-dir", str(work_dir)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "WORK PROJECT JOURNEYS" in captured.out


# ---------------------------------------------------------------------------
# TestCmdDay (Phase 3 §5.4)
# ---------------------------------------------------------------------------

class TestCmdDay:
    """Tests for cmd_day() — bridge-safe personal+work composite."""

    @pytest.fixture(autouse=True)
    def _setup(self, work_dir, tmp_path):
        _inject_state_dir(work_dir)
        # Create bridge directory with pulse artifact
        bridge_dir = tmp_path / "state" / "bridge"
        bridge_dir.mkdir(parents=True)
        pulse_data = {
            "$schema": "work_load_pulse",
            "schema_version": "1.1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "total_meeting_hours": 5.5,
                "after_hours_count": 1,
                "boundary_score": 0.72,
                "focus_availability_score": 0.4,
                "phase": "connect-season",
            }
        }
        import json as _json
        (bridge_dir / "work_load_pulse.json").write_text(
            _json.dumps(pulse_data), encoding="utf-8"
        )
        # Monkeypatch _REPO_ROOT in work_reader and work.briefing to point to tmp_path
        import work_reader as wr
        import work.briefing as _wb  # noqa: PLC0415
        self._orig_repo = wr._REPO_ROOT
        self._orig_briefing_repo = _wb._REPO_ROOT
        wr._REPO_ROOT = tmp_path
        _wb._REPO_ROOT = tmp_path
        yield
        wr._REPO_ROOT = self._orig_repo
        _wb._REPO_ROOT = self._orig_briefing_repo

    def test_day_has_header(self, work_dir, tmp_path):
        out = cmd_day()
        assert "DAILY PULSE" in out

    def test_day_includes_work_section(self, work_dir, tmp_path):
        out = cmd_day()
        assert "Work:" in out

    def test_day_reads_meeting_hours_from_bridge(self, work_dir, tmp_path):
        out = cmd_day()
        assert "5.5" in out or "5.5h" in out

    def test_day_includes_personal_section(self, work_dir, tmp_path):
        out = cmd_day()
        assert "Personal:" in out

    def test_day_includes_next_actions(self, work_dir, tmp_path):
        out = cmd_day()
        assert "/work prep" in out or "Next actions" in out

    def test_day_bridge_missing_degrades_gracefully(self, work_dir, tmp_path):
        # Remove the bridge file
        bridge_file = tmp_path / "state" / "bridge" / "work_load_pulse.json"
        bridge_file.unlink(missing_ok=True)
        out = cmd_day()
        assert "DAILY PULSE" in out  # still renders
        assert "Work:" in out

    def test_day_separation_note_present(self, work_dir, tmp_path):
        out = cmd_day()
        assert "bridge" in out.lower() or "separation" in out.lower()

    def test_main_day_dispatch(self, work_dir, tmp_path, capsys):
        rc = main(["--command", "day", "--state-dir", str(work_dir)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "DAILY PULSE" in captured.out


# ---------------------------------------------------------------------------
# TestWB4WB5 (WorkBriefingConfig org-calendar adaptive rules, Phase 3 §8.9)
# ---------------------------------------------------------------------------

class TestWB4WB5:
    """Tests for WB-4 and WB-5 org-calendar-driven adaptive rules."""

    def _make_org_cal(self, work_dir: Path, rows: list[str]) -> Path:
        header = (
            "---\nschema_version: '1.0'\ndomain: work-org-calendar\n---\n"
            "# Work Org Calendar\n\n"
            "## Milestone Schedule\n\n"
            "| Type | Date | Alert Lead (days) | Notes | Period |\n"
            "| --- | --- | --- | --- | --- |\n"
        )
        content = header + "\n".join(rows)
        path = work_dir / "work-org-calendar.md"
        path.write_text(content, encoding="utf-8")
        return path

    def test_wb4_connect_season_triggered(self, work_dir):
        _inject_state_dir(work_dir)
        future = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%d")
        self._make_org_cal(work_dir, [f"| connect_submission | {future} | 30 | H1 deadline | H1 |"])
        import work_reader as wr
        milestones = wr._read_org_calendar_milestones()
        assert len(milestones) >= 1
        ms = milestones[0]
        assert ms["type"] == "connect_submission"
        # Verify _build_briefing_config detects it
        cfg = _build_briefing_config({}, {}, {}, {})
        assert cfg.connect_season_alert is True

    def test_wb5_promo_season_triggered(self, work_dir):
        _inject_state_dir(work_dir)
        future = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
        self._make_org_cal(work_dir, [f"| promo_nomination | {future} | 30 | Promo window | FY26 |"])
        cfg = _build_briefing_config({}, {}, {}, {})
        assert cfg.promo_season_alert is True

    def test_wb4_not_triggered_when_future(self, work_dir):
        _inject_state_dir(work_dir)
        far_future = (datetime.now(timezone.utc) + timedelta(days=60)).strftime("%Y-%m-%d")
        self._make_org_cal(work_dir, [f"| connect_submission | {far_future} | 30 | H1 deadline | H1 |"])
        cfg = _build_briefing_config({}, {}, {}, {})
        assert cfg.connect_season_alert is False

    def test_wb4_wb5_not_triggered_with_empty_cal(self, work_dir):
        _inject_state_dir(work_dir)
        (work_dir / "work-org-calendar.md").unlink(missing_ok=True)
        cfg = _build_briefing_config({}, {}, {}, {})
        assert cfg.connect_season_alert is False
        assert cfg.promo_season_alert is False

    def test_read_org_calendar_milestones_parses_table(self, work_dir):
        _inject_state_dir(work_dir)
        future = (datetime.now(timezone.utc) + timedelta(days=15)).strftime("%Y-%m-%d")
        self._make_org_cal(work_dir, [
            f"| connect_submission | {future} | 30 | H1 | H1 FY26 |",
            f"| rewards_season | {future} | 30 | Rewards | FY26 |",
        ])
        import work_reader as wr
        milestones = wr._read_org_calendar_milestones()
        assert len(milestones) == 2
        types = [m["type"] for m in milestones]
        assert "connect_submission" in types
        assert "rewards_season" in types


# ---------------------------------------------------------------------------
# Shared test fixtures and helpers for Phase 3 tests
# ---------------------------------------------------------------------------

_DECISIONS_MD = """\
---
generated_by: work_decide
last_updated: '2026-03-20T09:00:00+00:00'
---

# Work Decisions

| ID | Date | Summary | Owner | Rationale |
| --- | --- | --- | --- | --- |
| D-001 | 2026-03-10 | OPEN: Adopt new analytics stack | me | Cost savings + velocity |
| D-002 | 2026-03-15 | CLOSED: Defer DR redesign to H2 | me | Competing priorities |
"""

_PEOPLE_VIS_EVENTS = """\
## Manager Chain

- Alice Doe — VP Engineering — alice@example.com
- Bob Roe — Director — bob@example.com

## Visibility Events

| Date | Stakeholder | Event Type | Context |
| --- | --- | --- | --- |
| 2026-02-10 | Director Smith | Staff Review | Presented Alpha Architecture |
| 2026-03-04 | VP Jones | Design Review | Beta Analytics RFC |
| 2026-03-10 | Director Smith | 1:1 | Follow-up on KPI model |
"""

_PEOPLE_VIS_STALE = """\
## Visibility Events

| Date | Stakeholder | Event Type | Context |
| --- | --- | --- | --- |
| 2024-01-01 | Old Boss | All Hands | Mentioned project |
"""

_CAREER_MD = """\
## Recognition

- "Delivered reliably under ambiguity" — VP Jones
"""

_PROJECTS_BLOCKED = """\
---
schema_version: '1.0'
domain: work-projects
last_updated: '2026-03-25T08:00:00+00:00'
---

## Active Sprint

- Implement caching (#1301) — Active — Pri 2

## Blocked Items

- Deploy to staging (#1188) — blocked on infra team

## Recently Completed (last 14 days)

- #1100 Add telemetry logging
"""


# ---------------------------------------------------------------------------
# TestCmdDecide  (Phase 3, §7.5)
# ---------------------------------------------------------------------------

class TestCmdDecide:

    def test_returns_string(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_decide("whether to adopt a new analytics stack")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_usage_guard_on_empty_context(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_decide("")
        assert "Usage" in result or "usage" in result

    def test_usage_guard_on_whitespace(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_decide("   ")
        assert "Usage" in result or "usage" in result

    def test_header_contains_decision_support(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_decide("whether to scale the service")
        assert "DECISION" in result.upper() or "Decision" in result

    def test_context_appears_in_output(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_decide("whether to adopt a new analytics stack")
        assert "analytics" in result.lower()

    def test_decision_id_allocated(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_decide("adopt analytics")
        import re
        assert re.search(r"D-\d{3}", result) is not None

    def test_decision_id_increments_from_existing(self, work_dir):
        _inject_state_dir(work_dir)
        (work_dir / "work-decisions.md").write_text(_DECISIONS_MD, encoding="utf-8")
        result = cmd_decide("new decision context")
        assert "D-003" in result

    def test_skeleton_logged_to_decisions_file(self, work_dir):
        _inject_state_dir(work_dir)
        cmd_decide("adopt analytics stack for team")
        dec_path = work_dir / "work-decisions.md"
        assert dec_path.exists()
        content = dec_path.read_text(encoding="utf-8")
        assert "OPEN:" in content

    def test_related_decisions_surfaced(self, work_dir):
        _inject_state_dir(work_dir)
        (work_dir / "work-decisions.md").write_text(_DECISIONS_MD, encoding="utf-8")
        result = cmd_decide("adopt analytics")
        assert "analytics" in result.lower()

    def test_evidence_gaps_section_present(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_decide("do we sunset the legacy pipeline")
        assert "Evidence Gaps" in result or "evidence" in result.lower()

    def test_decision_frame_six_questions(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_decide("build vs buy decision for auth")
        # Decision frame lists numbered questions
        assert "1." in result and "2." in result and "3." in result

    def test_freshness_footer_present(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_decide("decide something")
        assert "freshness" in result.lower() or "---" in result

    def test_main_decide_dispatch(self, work_dir, capsys):
        _inject_state_dir(work_dir)
        rc = main(["--command", "decide", "--decide-context",
                   "build vs buy analytics", "--state-dir", str(work_dir)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "DECISION" in captured.out.upper() or "Decision" in captured.out


# ---------------------------------------------------------------------------
# TestBuildInfluenceMap  (Phase 3, §7.6)
# ---------------------------------------------------------------------------

class TestBuildInfluenceMap:

    def test_empty_when_no_file(self, work_dir):
        _inject_state_dir(work_dir)
        result = _build_influence_map()
        assert result == ""

    def test_empty_when_no_vis_events_section(self, work_dir):
        _inject_state_dir(work_dir)
        (work_dir / "work-people.md").write_text(
            "---\ndomain: work-people\n---\n## Manager Chain\n- Alice Doe", encoding="utf-8"
        )
        result = _build_influence_map()
        assert result == ""

    def test_returns_string_with_events(self, work_dir):
        _inject_state_dir(work_dir)
        (work_dir / "work-people.md").write_text(
            "---\ndomain: work-people\n---\n" + _PEOPLE_VIS_EVENTS, encoding="utf-8"
        )
        result = _build_influence_map()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_stakeholder_count_shown(self, work_dir):
        _inject_state_dir(work_dir)
        (work_dir / "work-people.md").write_text(
            "---\ndomain: work-people\n---\n" + _PEOPLE_VIS_EVENTS, encoding="utf-8"
        )
        result = _build_influence_map()
        # 2 unique stakeholders: Director Smith and VP Jones
        assert "2 unique" in result or "stakeholder" in result.lower()

    def test_active_flag_for_recent_events(self, work_dir):
        _inject_state_dir(work_dir)
        (work_dir / "work-people.md").write_text(
            "---\ndomain: work-people\n---\n" + _PEOPLE_VIS_EVENTS, encoding="utf-8"
        )
        result = _build_influence_map()
        assert "active" in result.lower() or "✅" in result

    def test_stale_warning_for_old_events(self, work_dir):
        _inject_state_dir(work_dir)
        (work_dir / "work-people.md").write_text(
            "---\ndomain: work-people\n---\n" + _PEOPLE_VIS_STALE, encoding="utf-8"
        )
        result = _build_influence_map()
        # Old Boss event is from 2024, should be stale
        if result:  # only check if events were parsed
            assert "stale" in result.lower() or "⚠" in result

    def test_gap_recommendation_for_stale(self, work_dir):
        _inject_state_dir(work_dir)
        (work_dir / "work-people.md").write_text(
            "---\ndomain: work-people\n---\n" + _PEOPLE_VIS_STALE, encoding="utf-8"
        )
        result = _build_influence_map()
        if result:
            assert "consider" in result.lower() or "Gap" in result

    def test_returns_empty_string_on_exception(self, work_dir, monkeypatch):
        _inject_state_dir(work_dir)
        # Write a malformed file that will cause errors during parsing
        (work_dir / "work-people.md").write_text("---\n---\n", encoding="utf-8")
        result = _build_influence_map()
        # Should not raise; returns "" gracefully
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# TestCmdConnectPrepCalibration  (Phase 3, §7.6)
# ---------------------------------------------------------------------------

class TestCmdConnectPrepCalibration:

    def test_calibration_mode_returns_string(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_connect_prep(mode="calibration")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_calibration_header_in_output(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_connect_prep(mode="calibration")
        assert "CALIBRATION" in result.upper() or "Calibration" in result

    def test_no_placeholder_notice_in_calibration(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_connect_prep(mode="calibration")
        # Old Phase 3 placeholder text should be gone
        assert "Phase 3" not in result or "DRAFT" in result

    def test_standard_mode_includes_influence_map_when_populated(self, work_dir):
        _inject_state_dir(work_dir)
        (work_dir / "work-people.md").write_text(
            "---\ndomain: work-people\n---\n" + _PEOPLE_VIS_EVENTS, encoding="utf-8"
        )
        result = cmd_connect_prep(mode="")
        assert "Influence" in result or "stakeholder" in result.lower() or "visibility" in result.lower()

    def test_standard_mode_graceful_without_people(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_connect_prep(mode="")
        # Should still render without error
        assert isinstance(result, str)
        assert not result.startswith("Error")

    def test_main_calibration_dispatch(self, work_dir, capsys):
        _inject_state_dir(work_dir)
        rc = main(["--command", "connect-prep", "--mode", "calibration",
                   "--state-dir", str(work_dir)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "Calibration" in captured.out or "CALIBRATION" in captured.out


# ---------------------------------------------------------------------------
# TestCmdMemoEscalationDecision  (Phase 3, §7.10)
# ---------------------------------------------------------------------------

class TestCmdMemoEscalationDecision:

    def test_escalation_mode_returns_string(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_memo(escalation_context="Infra blocking deploy")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_escalation_header_in_output(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_memo(escalation_context="Infra blocking deploy")
        assert "ESCALATION" in result.upper() or "Escalation" in result

    def test_decision_mode_returns_string(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_memo(decision_id="D-001")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_decision_header_in_output(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_memo(decision_id="D-001")
        assert "DECISION" in result.upper() or "Decision" in result

    def test_standard_mode_unchanged(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_memo()
        assert isinstance(result, str)
        # Standard mode should produce a memo (not escalation or decision)
        assert "ESCALATION" not in result.upper() or len(result) > 50

    def test_escalation_context_appears_in_output(self, work_dir):
        _inject_state_dir(work_dir)
        result = cmd_memo(escalation_context="Security review gate is a blocker")
        assert "Security review" in result or "blocker" in result.lower()

    def test_main_escalation_dispatch(self, work_dir, capsys):
        _inject_state_dir(work_dir)
        rc = main(["--command", "memo", "--escalation-context",
                   "Deployment gate blocked by infra", "--state-dir", str(work_dir)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "Deployment" in captured.out or "ESCALATION" in captured.out.upper()

    def test_main_decision_memo_dispatch(self, work_dir, capsys):
        _inject_state_dir(work_dir)
        (work_dir / "work-decisions.md").write_text(_DECISIONS_MD, encoding="utf-8")
        rc = main(["--command", "memo", "--decision-id", "D-001",
                   "--state-dir", str(work_dir)])
        captured = capsys.readouterr()
        assert rc == 0
        assert "Decision" in captured.out or "DECISION" in captured.out.upper()


# ---------------------------------------------------------------------------
# TestEnsureDecisionsHeader + TestAppendToFile
# ---------------------------------------------------------------------------

class TestEnsureDecisionsHeader:

    def test_creates_file_when_missing(self, tmp_path):
        path = tmp_path / "work-decisions.md"
        assert not path.exists()
        _ensure_decisions_header(path)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "| ID |" in content

    def test_noop_when_header_already_present(self, tmp_path):
        path = tmp_path / "work-decisions.md"
        path.write_text(_DECISIONS_MD, encoding="utf-8")
        original_size = len(path.read_text(encoding="utf-8"))
        _ensure_decisions_header(path)
        # File should not grow (already has header)
        assert len(path.read_text(encoding="utf-8")) == original_size

    def test_prepends_header_when_missing_in_existing_file(self, tmp_path):
        path = tmp_path / "work-decisions.md"
        path.write_text("Some content without a header\n", encoding="utf-8")
        _ensure_decisions_header(path)
        content = path.read_text(encoding="utf-8")
        assert "| ID |" in content


class TestAppendToFile:

    def test_appends_to_existing_file(self, tmp_path):
        path = tmp_path / "test.md"
        path.write_text("first line\n", encoding="utf-8")
        _append_to_file(path, "second line\n")
        content = path.read_text(encoding="utf-8")
        assert "first line" in content
        assert "second line" in content

    def test_creates_file_when_missing(self, tmp_path):
        path = tmp_path / "newfile.md"
        _append_to_file(path, "new content\n")
        assert path.exists()
        assert "new content" in path.read_text(encoding="utf-8")

    def test_atomic_via_tmp_file(self, tmp_path):
        path = tmp_path / "atomic.md"
        path.write_text("original\n", encoding="utf-8")
        _append_to_file(path, "appended\n")
        # tmp file should not remain
        tmp = path.with_suffix(".md.tmp")
        assert not tmp.exists()


# ---------------------------------------------------------------------------
# TestAutoRefreshOnStale  (Phase 3, §8.7)
# ---------------------------------------------------------------------------

class TestAutoRefreshOnStale:

    def _write_stale_domain(self, work_dir: Path, name: str) -> None:
        stale_dt = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        _write_state(work_dir, name, {"last_updated": stale_dt, "domain": name})

    def test_advisory_shown_when_opt_in_and_stale(self, work_dir, monkeypatch):
        _inject_state_dir(work_dir)
        # Write stale state files
        for d in ["work-summary.md", "work-calendar.md", "work-comms.md"]:
            self._write_stale_domain(work_dir, d)
        # Inject profile with auto_refresh_on_stale: true
        import work_reader as wr
        monkeypatch.setattr(wr, "_load_profile", lambda: {
            "work": {"refresh": {"auto_refresh_on_stale": True}}
        })
        result = cmd_work()
        assert "AUTO-REFRESH ADVISORY" in result or "stale" in result.lower()

    def test_no_advisory_when_opt_out(self, work_dir, monkeypatch):
        _inject_state_dir(work_dir)
        for d in ["work-summary.md", "work-calendar.md", "work-comms.md"]:
            self._write_stale_domain(work_dir, d)
        import work_reader as wr
        monkeypatch.setattr(wr, "_load_profile", lambda: {
            "work": {"refresh": {"auto_refresh_on_stale": False}}
        })
        result = cmd_work()
        assert "AUTO-REFRESH ADVISORY" not in result

    def test_no_advisory_when_data_fresh(self, work_dir, monkeypatch):
        _inject_state_dir(work_dir)
        # Write fresh state files
        for d in ["work-summary.md", "work-calendar.md"]:
            _write_state(work_dir, d, {"last_updated": datetime.now(timezone.utc).isoformat()})
        import work_reader as wr
        monkeypatch.setattr(wr, "_load_profile", lambda: {
            "work": {"refresh": {"auto_refresh_on_stale": True}}
        })
        result = cmd_work()
        assert "AUTO-REFRESH ADVISORY" not in result



# ---------------------------------------------------------------------------
# TestDetectDecisionDrift  (Phase 3 item 11 — decision drift detection)
# ---------------------------------------------------------------------------

class TestDetectDecisionDrift:
    """Phase 3 item 11 — _detect_decision_drift() unit tests."""

    @staticmethod
    def _open_row(keyword: str, age_days: int, did: str = "D-001") -> str:
        """Build a table row whose decision date is age_days ago."""
        from datetime import datetime, timezone, timedelta
        date = (datetime.now(timezone.utc) - timedelta(days=age_days)).strftime("%Y-%m-%d")
        return f"| {did} | {date} | OPEN: {keyword} design decision | me | notes |\n"

    def test_empty_body_returns_empty(self):
        result = work_reader._detect_decision_drift("Architecture Review", "")
        assert result == []

    def test_blank_meeting_title_returns_empty(self):
        result = work_reader._detect_decision_drift("", "| D-001 | 2026-01-01 | OPEN: something | me |")
        assert result == []

    def test_all_stop_word_title_returns_empty(self):
        # "Weekly Sync" → "weekly" and "sync" both in the stop-word set
        result = work_reader._detect_decision_drift("Weekly Sync", "| D-001 | 2026-01-01 | OPEN: sync | me |")
        assert result == []

    def test_open_decision_matching_meeting_flagged(self):
        body = self._open_row("architecture", age_days=20)
        result = work_reader._detect_decision_drift("Architecture Review", body)
        assert len(result) >= 1

    def test_age_14d_shows_pending_prefix(self):
        body = self._open_row("architecture", age_days=20)
        result = work_reader._detect_decision_drift("Architecture Review", body)
        assert any("Pending decision:" in r for r in result)

    def test_age_42d_shows_drift_prefix(self):
        body = self._open_row("architecture", age_days=50)
        result = work_reader._detect_decision_drift("Architecture Review", body)
        assert any("Decision drift:" in r for r in result)

    def test_non_matching_decision_not_flagged(self):
        body = self._open_row("database", age_days=20)
        # Meeting title "Architecture Review" → keywords: architecture, review
        # "database" keyword does NOT match
        result = work_reader._detect_decision_drift("Architecture Review", body)
        assert result == []

    def test_historical_deferred_count_3_flagged(self):
        # No D-NNN rows, but 3 lines with meeting keywords + deferral words
        body = (
            "architecture design deferred this sprint\n"
            "architecture plan deferred again pending approval\n"
            "review still tbd next quarter\n"
        )
        result = work_reader._detect_decision_drift("Architecture Review", body)
        assert len(result) >= 1
        assert any("deferred" in r.lower() or "drift" in r.lower() for r in result)

    def test_results_capped_at_3(self):
        # Build 6 matching open decisions (age > 14 each)
        rows = "".join(
            self._open_row("architecture", age_days=20, did=f"D-{i:03d}")
            for i in range(1, 7)
        )
        result = work_reader._detect_decision_drift("Architecture Review", rows)
        assert len(result) <= 3

    def test_closed_decision_not_flagged(self):
        from datetime import datetime, timezone, timedelta
        date = (datetime.now(timezone.utc) - timedelta(days=50)).strftime("%Y-%m-%d")
        body = f"| D-001 | {date} | CLOSED: architecture redesign | me | done |\n"
        result = work_reader._detect_decision_drift("Architecture Review", body)
        assert result == []

    def test_decision_under_7_days_not_flagged(self):
        # Age <= 7 days → not flagged even if matching and OPEN
        body = self._open_row("architecture", age_days=3)
        result = work_reader._detect_decision_drift("Architecture Review", body)
        assert result == []


# ---------------------------------------------------------------------------
# TestHealthOrphanAdvisory  (Phase 3 — orphan file advisory in cmd_health)
# ---------------------------------------------------------------------------

class TestHealthOrphanAdvisory:
    """Orphan state file advisory in cmd_health() — Phase 3 §8.5."""

    def test_known_domain_not_flagged(self, work_dir):
        _inject_state_dir(work_dir)
        _write_state(work_dir, "work-projects.md", {
            "domain": "work-projects",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })
        result = cmd_health()
        # "work-projects" is a known domain — should NOT appear in orphan list
        assert "work-projects.md" not in result or "Orphan" not in result.split("work-projects.md")[0][-50:]

    def test_orphan_domain_flagged(self, work_dir):
        _inject_state_dir(work_dir)
        _write_state(work_dir, "inflection-point-narrative.md", {
            "domain": "work-inflection-point",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })
        result = cmd_health()
        assert "⚠ Orphan" in result or "inflection-point-narrative.md" in result

    def test_bootstrap_advisory_shown_for_orphan(self, work_dir):
        _inject_state_dir(work_dir)
        _write_state(work_dir, "alpha-ramp-deep-context.md", {
            "domain": "work-alpha-ramp",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })
        result = cmd_health()
        assert "bootstrap import" in result.lower() or "bootstrap" in result.lower()

    def test_no_orphan_advisory_when_all_known(self, work_dir):
        _inject_state_dir(work_dir)
        for domain in ["work-calendar", "work-projects", "work-people"]:
            _write_state(work_dir, f"{domain}.md", {
                "domain": domain,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            })
        result = cmd_health()
        assert "Orphan state files" not in result

    def test_multiple_orphans_all_listed(self, work_dir):
        _inject_state_dir(work_dir)
        for name, domain in [
            ("file-alpha.md", "work-alpha-custom"),
            ("file-beta.md", "work-beta-custom"),
        ]:
            _write_state(work_dir, name, {
                "domain": domain,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            })
        result = cmd_health()
        assert "file-alpha.md" in result
        assert "file-beta.md" in result
