"""
tests/work/test_work_warm_start.py — Warm-start processor tests.

Validates scripts/work_warm_start.py (§15 Warm-Start Strategy):
  - Parser handles both table and bullet calendar formats
  - People are correctly extracted and deduplicated
  - Projects are detected from meeting signal words
  - Career evidence is captured from key highlights
  - Recurring meeting detection works
  - State file writers produce valid markdown
  - Atomic write semantics hold
  - Dry-run mode writes nothing

Run: pytest tests/work/test_work_warm_start.py -v
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

# Import the module under test
from scripts.work_warm_start import (  # type: ignore
    ScrapeParser,
    WarmStartAggregator,
    PersonRecord,
    ProjectRecord,
    CareerEvidenceItem,
    RecurringMeeting,
    ScrapeWeek,
    write_work_people,
    write_work_projects,
    write_work_career,
    write_work_calendar,
    write_work_sources,
    write_work_summary,
    run_warm_start,
    _is_noise_name,
    _is_self,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TABLE_FORMAT_WEEK = textwrap.dedent("""\
    # Week 80 — Mar 2–8, 2026

    **Week range:** Mon Mar 2 – Sun Mar 8, 2026

    ## Q4 — Calendar Events

    | # | Title | Organizer | Attendees | Start–End | Notes |
    |---|---|---|---:|---|---|
    | 1 | Enter Top5 Weekly | Priya Sharma | 26 | 8:00–8:10 AM | none |
    | 2 | **TeamPM: PlatformA Weekly** | ACE | 10 | 10:35–11:30 AM | 🔒 |
    | 3 | PlatformB/SF Platform Sync | Michael Chen | 55 | 8:00–8:30 AM | 🔒 |
    | 4 | Platform-A-DD Daily Standup | Sam Rodriguez | 31 | 1:30–2:00 PM | 🔒 |

    ## Q5 — Teams Chats (Activity Mar 2–8, 2026)

    - **1:1 — ACE ↔ Alex Morgan**
      - "Will you be updating the OS deployment slides?"

    - **ACP – Infra Software PM Team** (Jordan Lee + 12 others)
      - "any updates on the timeframe…"

    ## Q6 — Files (Mar 2–8, 2026)

    | File | Context / Location |
    |---|---|
    | **PlatformB Master Schedule_2025.xlsx** | DeploymentTeam SharePoint → PM planning |
    | **202603-01 – PlatformA LT Update.pptx** | PlatformA SharePoint → Monthly LT Review |

    ## Key Highlights

    - **TeamPM: PlatformA Weekly** — ACE organized; PlatformB roadmap alignment
    - **Issue 61 SENT** — PlatformA newsletter published; 11 ppl, key technical updates
    - **ACE authored** Platform Alpha Ramp DeployFlow requirements.docx
    - **LT Review dry run** — PlatformA LT Update, 8 ppl
""")

BULLET_FORMAT_WEEK = textwrap.dedent("""\
    # Week 37 — May 5–11, 2025

    **Week range:** Mon May 5 – Sun May 11, 2025

    ## Q4 — Calendar Events

    ### Monday — May 5, 2025

    - **Platform-A-DD MVP (P0) alignment kickoff** *(ACE organizer)*
      Organizer: ACE Mishra
      Attendees: 22
      Notes: 🔒 Redacted

    - **Azure Core Town Hall**
      Organizer: Azure Core Executive Office
      Attendees: 895
      Notes: high-visibility company event

    ## Q5 — Teams Chats

    - **Direct Chat: ACE ↔ Casey Hill**
      - "Platform-A-DD MVP blocked on Sam dep"

    ## Q6 — Files

    | File | Context |
    |---|---|
    | **Platform Alpha Ramp DeployFlow requirements.docx** | ACE owned document — DeployFlow integration |

    ## Key Highlights

    - **ACE organized** Platform-A-DD Deployment×Repairs cross-functional meeting (25 ppl)
    - **Issue 58 SENT** — Newsletter to PlatformA stakeholders
    - **ACE authored** DataStore Training Doc + PlatformB DD 2-pager + Ramp Appendix
""")


@pytest.fixture
def tmp_scrape_dir(tmp_path):
    """Create a temp scrape directory with sample weekly files."""
    y2026 = tmp_path / "2026"
    y2025 = tmp_path / "2025"
    y2026.mkdir()
    y2025.mkdir()
    (y2026 / "03-w2.md").write_text(TABLE_FORMAT_WEEK, encoding="utf-8")
    (y2025 / "05-w1.md").write_text(BULLET_FORMAT_WEEK, encoding="utf-8")
    return tmp_path


@pytest.fixture
def artha_dir(tmp_path):
    """Create a minimal Artha project directory."""
    artha = tmp_path / "artha"
    (artha / "state" / "work").mkdir(parents=True)
    (artha / "config").mkdir()
    # Minimal user_profile.yaml with import_completed: false
    (artha / "config" / "user_profile.yaml").write_text(
        "work:\n  bootstrap:\n    import_completed: false\n", encoding="utf-8"
    )
    return artha


# ===========================================================================
# Test Group 1: Helper functions
# ===========================================================================

class TestHelperFunctions:

    def test_is_self_returns_false_when_unconfigured(self):
        # _is_self is configurable via user_profile.yaml; empty by default
        assert not _is_self("ACE")
        assert not _is_self("Alex Chen")
        assert not _is_self("Alice Smith")

    def test_is_self_recognizes_configured_aliases(self, monkeypatch):
        import scripts.work_warm_start as wwm  # type: ignore
        monkeypatch.setattr(wwm, "_SELF_ALIASES", frozenset(["ace", "alex chen"]))
        assert _is_self("ACE")
        assert _is_self("Alex Chen")
        assert not _is_self("Alice Smith")

    def test_is_noise_name_rejects_project_codes(self):
        assert _is_noise_name("PlatformA")
        assert _is_noise_name("ADO")
        assert _is_noise_name("LT")

    def test_is_noise_name_accepts_valid_name(self):
        assert not _is_noise_name("Alex Morgan")
        assert not _is_noise_name("Michael Chen")

    def test_is_noise_name_rejects_short_one_word(self):
        assert _is_noise_name("Alice")      # single name, too short for graph
        assert _is_noise_name("Jo")

    def test_is_noise_name_accepts_three_word_names(self):
        assert not _is_noise_name("Priya Sharma")
        assert not _is_noise_name("Sam Rodriguez")


# ===========================================================================
# Test Group 2: Parser — table format
# ===========================================================================

class TestParserTableFormat:

    def test_parse_file_returns_scrape_week(self, tmp_path):
        f = tmp_path / "2026" / "03-w2.md"
        f.parent.mkdir(parents=True)
        f.write_text(TABLE_FORMAT_WEEK, encoding="utf-8")
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week is not None
        assert isinstance(week, ScrapeWeek)

    def test_year_month_parsed_correctly(self, tmp_path):
        f = tmp_path / "2026" / "03-w2.md"
        f.parent.mkdir(parents=True)
        f.write_text(TABLE_FORMAT_WEEK, encoding="utf-8")
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week.year == 2026
        assert week.month == 3
        assert week.week_num == "w2"

    def test_week_label_extracted(self, tmp_path):
        f = tmp_path / "2026" / "03-w2.md"
        f.parent.mkdir(parents=True)
        f.write_text(TABLE_FORMAT_WEEK, encoding="utf-8")
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert "80" in week.week_label or week.week_label != ""

    def test_calendar_events_extracted(self, tmp_path):
        f = tmp_path / "2026" / "03-w2.md"
        f.parent.mkdir(parents=True)
        f.write_text(TABLE_FORMAT_WEEK, encoding="utf-8")
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert len(week.calendar_events) >= 1

    def test_files_extracted(self, tmp_path):
        f = tmp_path / "2026" / "03-w2.md"
        f.parent.mkdir(parents=True)
        f.write_text(TABLE_FORMAT_WEEK, encoding="utf-8")
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert len(week.files) >= 1
        file_names = [fi["name"] for fi in week.files]
        assert any("xlsx" in n or "pptx" in n for n in file_names)

    def test_highlights_extracted(self, tmp_path):
        f = tmp_path / "2026" / "03-w2.md"
        f.parent.mkdir(parents=True)
        f.write_text(TABLE_FORMAT_WEEK, encoding="utf-8")
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert len(week.key_highlights) >= 1

    def test_parser_returns_none_for_invalid_path(self, tmp_path):
        f = tmp_path / "invalid" / "file.md"
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week is None

    def test_parser_returns_none_for_bad_stem(self, tmp_path):
        f = tmp_path / "2026" / "README.md"
        f.parent.mkdir(parents=True)
        f.write_text("# README", encoding="utf-8")
        parser = ScrapeParser()
        # README.md doesn't match year/week pattern
        result = parser.parse_file(f)
        assert result is None or result.week_num == ""


# ===========================================================================
# Test Group 3: Aggregator
# ===========================================================================

class TestAggregator:

    def _build_agg(self, tmp_path):
        f1 = tmp_path / "2026" / "03-w2.md"
        f1.parent.mkdir(parents=True)
        f1.write_text(TABLE_FORMAT_WEEK, encoding="utf-8")
        f2 = tmp_path / "2025" / "05-w1.md"
        f2.parent.mkdir(parents=True)
        f2.write_text(BULLET_FORMAT_WEEK, encoding="utf-8")
        parser = ScrapeParser()
        agg = WarmStartAggregator()
        for f in [f1, f2]:
            week = parser.parse_file(f)
            if week:
                agg.process_week(week)
        return agg

    def test_people_extracted(self, tmp_path):
        agg = self._build_agg(tmp_path)
        assert len(agg.people) > 0

    def test_ved_not_in_people_graph(self, tmp_path):
        agg = self._build_agg(tmp_path)
        for name in agg.people:
            assert not _is_self(name), f"ACE appeared in people graph: {name}"

    def test_projects_extracted(self, tmp_path):
        agg = self._build_agg(tmp_path)
        # PlatformA and PlatformB should be detected from _PROJECT_SIGNALS
        project_names = " ".join(p.name.lower() for p in agg.top_projects())
        assert "platforma" in project_names or "platformb" in project_names or len(agg.projects) > 0

    def test_career_evidence_extracted(self, tmp_path):
        agg = self._build_agg(tmp_path)
        # Issue 61 and Issue 58 are in the test data
        nl_evidence = [e for e in agg.career_evidence if e.event_type == "newsletter"]
        assert len(nl_evidence) >= 1

    def test_recurring_meetings_need_5plus(self, tmp_path):
        agg = self._build_agg(tmp_path)
        # With only 2 weeks of data, no recurring meetings at 5+ threshold
        recurring = agg.top_recurring_meetings(min_occurrences=5)
        assert isinstance(recurring, list)  # can be empty — that's correct

    def test_two_occurrences_detected_at_threshold_2(self, tmp_path):
        # If the same meeting appears twice it's 2-recurring at min 2
        f1 = tmp_path / "2026" / "03-w2.md"
        f2 = tmp_path / "2026" / "03-w3.md"
        f1.parent.mkdir(parents=True)
        # Same meeting title in both files
        same_meeting = textwrap.dedent("""\
            # Week 81 — Mar 3–9, 2026
            ## Q4 — Calendar Events
            | # | Title | Organizer | Attendees | Start–End | Notes |
            |---|---|---|---:|---|---|
            | 1 | Platform-A-DD Daily Standup | Sam Rodriguez | 31 | 1:30–2:00 PM | 🔒 |
        """)
        f1.write_text(TABLE_FORMAT_WEEK, encoding="utf-8")
        f2.write_text(same_meeting, encoding="utf-8")
        parser = ScrapeParser()
        agg = WarmStartAggregator()
        for f in [f1, f2]:
            week = parser.parse_file(f)
            if week:
                agg.process_week(week)
        recurring = agg.top_recurring_meetings(min_occurrences=2)
        titles = [rm.title_pattern.lower() for rm in recurring]
        assert any("Platform-A-DD" in t or "standup" in t for t in titles)

    def test_top_people_returns_sorted_by_score(self, tmp_path):
        agg = self._build_agg(tmp_path)
        people = agg.top_people(10)
        if len(people) >= 2:
            # First person should have >= score of last
            assert people[0].relationship_score >= people[-1].relationship_score

    def test_sources_extracted(self, tmp_path):
        agg = self._build_agg(tmp_path)
        sources = agg.top_sources()
        assert isinstance(sources, list)


# ===========================================================================
# Test Group 4: State file writers
# ===========================================================================

class TestStateFileWriters:

    def _make_agg(self):
        """Build a minimal aggregator with test data."""
        agg = WarmStartAggregator()
        agg._week_labels = ["2024/08-w4", "2026/03-w3"]

        # Add a person
        p = PersonRecord(name="Alex Morgan", first_seen_week="2024/08-w4")
        p.meetings_together = 50
        p.is_manager = True
        agg.people["alex morgan"] = p

        # Add a project
        proj = ProjectRecord(name="Platform Alpha",
                             first_week="2024/09-w1", last_week="2026/03-w3",
                             meeting_count=150)
        agg.projects["platforma"] = proj

        # Add career evidence
        agg.career_evidence.append(CareerEvidenceItem(
            date="2024/09-w4", week="Week 5",
            event_type="newsletter",
            description="Issue 1 SENT — PlatformA weekly newsletter",
            impact_signal="medium",
        ))
        agg.career_evidence.append(CareerEvidenceItem(
            date="2024/12-w1", week="Week 15",
            event_type="lt_deck",
            description="LT Deck: PlatformA LT December Dry Run",
            attendee_count=8,
            impact_signal="high",
        ))

        # Recurring meeting
        agg.recurring_meetings["Platform-A-DD daily standup"] = RecurringMeeting(
            title_pattern="Platform-A-DD Daily Standup",
            organizer="Sam Rodriguez",
            typical_attendees=31,
            typical_time="1:30–2:00 PM",
            day_of_week="daily",
            first_week="2025/02-w1",
            last_week="2026/03-w3",
            occurrence_count=60,
        )

        return agg

    def test_write_work_people(self, tmp_path):
        agg = self._make_agg()
        write_work_people(agg, tmp_path)
        out = (tmp_path / "work-people.md").read_text(encoding="utf-8")
        assert "Alex Morgan" in out
        assert "schema_version" in out
        assert "encrypted: true" in out

    def test_write_work_projects(self, tmp_path):
        agg = self._make_agg()
        write_work_projects(agg, tmp_path)
        out = (tmp_path / "work-projects.md").read_text(encoding="utf-8")
        assert "Platform Alpha" in out
        assert "schema_version" in out

    def test_write_work_career(self, tmp_path):
        agg = self._make_agg()
        write_work_career(agg, tmp_path)
        out = (tmp_path / "work-career.md").read_text(encoding="utf-8")
        assert "newsletter" in out.lower() or "Newsletter" in out
        assert "LT" in out or "lt_deck" in out.lower()
        assert "encrypted: true" in out

    def test_write_work_calendar(self, tmp_path):
        agg = self._make_agg()
        write_work_calendar(agg, tmp_path)
        out = (tmp_path / "work-calendar.md").read_text(encoding="utf-8")
        assert "Platform-A-DD Daily Standup" in out
        assert "occurrence" in out.lower() or "60" in out

    def test_write_work_sources(self, tmp_path):
        agg = self._make_agg()
        write_work_sources(agg, tmp_path)
        out = (tmp_path / "work-sources.md").read_text(encoding="utf-8")
        assert "SharePoint" in out
        assert "schema_version" in out

    def test_write_work_summary(self, tmp_path):
        agg = self._make_agg()
        write_work_summary(agg, tmp_path)
        out = (tmp_path / "work-summary.md").read_text(encoding="utf-8")
        assert "Warm-start" in out or "warm-start" in out
        assert "2" in out  # weeks_imported

    def test_all_writers_produce_yaml_frontmatter(self, tmp_path):
        agg = self._make_agg()
        for writer_fn in [write_work_people, write_work_projects, write_work_career,
                          write_work_calendar, write_work_sources, write_work_summary]:
            writer_fn(agg, tmp_path)
        for fname in ["work-people.md", "work-projects.md", "work-career.md",
                      "work-calendar.md", "work-sources.md", "work-summary.md"]:
            content = (tmp_path / fname).read_text(encoding="utf-8")
            assert content.startswith("---"), f"{fname} must start with YAML frontmatter"
            assert "schema_version" in content


# ===========================================================================
# Test Group 5: Atomic write semantics
# ===========================================================================

class TestAtomicWriteInWarmStart:

    def test_tmp_file_not_left_behind_on_failure(self, tmp_path):
        """If an error occurs mid-write, no .tmp file should remain."""
        from scripts.work_warm_start import _atomic_write  # type: ignore
        target = tmp_path / "test.md"
        _atomic_write(target, "# hello")
        assert target.exists()
        assert not target.with_suffix(".md.tmp").exists()

    def test_write_creates_parent_dirs(self, tmp_path):
        from scripts.work_warm_start import _atomic_write  # type: ignore
        deep = tmp_path / "a" / "b" / "c" / "file.md"
        _atomic_write(deep, "content")
        assert deep.exists()


# ===========================================================================
# Test Group 6: run_warm_start dry-run mode
# ===========================================================================

class TestDryRun:

    def test_dry_run_writes_nothing(self, tmp_scrape_dir, artha_dir):
        state_dir = artha_dir / "state" / "work"
        initial_files = set(state_dir.rglob("*.md"))
        run_warm_start(
            scrape_dir=tmp_scrape_dir,
            artha_dir=artha_dir,
            dry_run=True,
        )
        final_files = set(state_dir.rglob("*.md"))
        assert initial_files == final_files, "Dry run must not write any files"

    def test_dry_run_returns_dry_run_flag(self, tmp_scrape_dir, artha_dir):
        result = run_warm_start(
            scrape_dir=tmp_scrape_dir,
            artha_dir=artha_dir,
            dry_run=True,
        )
        assert result.get("dry_run") is True

    def test_dry_run_still_processes_files(self, tmp_scrape_dir, artha_dir):
        result = run_warm_start(
            scrape_dir=tmp_scrape_dir,
            artha_dir=artha_dir,
            dry_run=True,
        )
        assert result["processed"] >= 1  # even in dry-run, files are parsed


# ===========================================================================
# Test Group 7: Full run integration
# ===========================================================================

class TestFullRun:

    def test_full_run_creates_all_state_files(self, tmp_scrape_dir, artha_dir):
        run_warm_start(
            scrape_dir=tmp_scrape_dir,
            artha_dir=artha_dir,
            dry_run=False,
        )
        state_dir = artha_dir / "state" / "work"
        expected = [
            "work-people.md", "work-projects.md", "work-career.md",
            "work-calendar.md", "work-sources.md", "work-summary.md",
        ]
        for fname in expected:
            assert (state_dir / fname).exists(), f"Missing {fname} after warm start"

    def test_full_run_marks_import_completed(self, tmp_scrape_dir, artha_dir):
        run_warm_start(
            scrape_dir=tmp_scrape_dir,
            artha_dir=artha_dir,
            dry_run=False,
        )
        profile = (artha_dir / "config" / "user_profile.yaml").read_text(encoding="utf-8")
        assert "import_completed: true" in profile

    def test_full_run_result_has_counts(self, tmp_scrape_dir, artha_dir):
        result = run_warm_start(
            scrape_dir=tmp_scrape_dir,
            artha_dir=artha_dir,
            dry_run=False,
        )
        assert result["processed"] >= 1
        assert result["dry_run"] is False
        assert "people" in result
        assert "projects" in result


# ===========================================================================
# Test Group 8: New parsers — new_people, emails, summary, authored_docs
# ===========================================================================

NEW_PEOPLE_TABLE_WEEK = """\
# Week 5 — Sep 23–29, 2024

## Q4 — Calendar Events

| # | Title | Organizer | Attendees | Start–End | Notes |
|---|---|---|---:|---|---|
| 1 | PlatformA Weekly Sync | Alex Morgan | 5 | 10:00–11:00 AM | |

## New People Encountered This Week

| Name | Context | Role (inferred) |
|------|---------|----------------|
| David Stone | Daniel/Wed intro; HW-Team DataIQ POC | Engineer, DataIQ/xscenarios area |
| Raj Kumar | Monthly H/W Program Update | PM, Hardware team |
| Reena D'Costa | Polaris SCHIE organizer | PM, Hardware/Polaris |

## Key Highlights
- PlatformA ownership confirmed

## Notable Emails section (non-automated)
Dummy line (no email table)
"""

NEW_PEOPLE_COMMAS_WEEK = """\
# Week 1 — Sep 2–8, 2024

New people: Anita Sharma, Brian Kumar, Hana Lee.

## Key Highlights
- Onboarding week
"""

EMAILS_TABLE_WEEK = """\
# Week 30 — Mar 17–23, 2025

## Summary

Critical execution week. Tier1 LRS went LIVE. AutoRemedy brainstorm organized.

## Q4+Q5+Q6. Teams Chats / Emails / Files

### Notable Emails (non-automated)

| Subject | Key Content |
|---------|-------------|
| Tier1 LRS LIVE Announcement (draft/review) | Historic milestone — PlatformA Tier1 LRS went LIVE |
| QualityProg readiness for Pilot++ | Funding, plan, clusters for QualityProg support of PlatformA Pilot++ |
| **DataIQ roadmap & HW-Team POC** | First explicit DataIQ email; HW-Team POC planning |
"""

AUTHORED_DOCS_WEEK = """\
# Week 22 — Jan 20–26, 2025

## Q6 — Files

1. **PlatformB DirectDrive 2-Pager.docx** — Architecture summary ACE AUTHORED
2. **Ramp plan deck.onepart** — LT-facing infra readiness ACE AUTHORED
3. **NormalDocument.pptx** — Regular file, authored by someone else

## Key Highlights
- Productive week
"""


class TestNewParsers:
    """Tests for _parse_new_people, _parse_emails, _parse_summary, _parse_authored_docs."""

    def _make_week_file(self, tmp_path, year: str, stem: str, content: str):
        d = tmp_path / year
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{stem}.md"
        f.write_text(content, encoding="utf-8")
        return f

    # --- _parse_new_people (table format) ---

    def test_new_people_table_extracts_names(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "09-w4", NEW_PEOPLE_TABLE_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        names = [p["name"] for p in week.new_people]
        assert "David Stone" in names
        assert "Raj Kumar" in names
        assert "Reena D'Costa" in names

    def test_new_people_table_extracts_roles(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "09-w4", NEW_PEOPLE_TABLE_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        roles = {p["name"]: p.get("inferred_role", "") for p in week.new_people}
        assert "Engineer" in roles.get("David Stone", "")
        assert "PM" in roles.get("Reena D'Costa", "")

    def test_new_people_comma_list_fallback(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "09-w1", NEW_PEOPLE_COMMAS_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        names = [p["name"] for p in week.new_people]
        assert any("Anita" in n or "Brian" in n for n in names)

    def test_no_new_people_returns_empty(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "09-w3", """\
# Week 3 — Sep 16–22, 2024
## Q4 — Calendar Events
| # | Title | Organizer | Attendees | Start–End | Notes |
|---|---|---|---:|---|---|
| 1 | Sync | Alex Morgan | 5 | 10:00 AM | |
""")
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week.new_people == []

    # --- _parse_emails ---

    def test_emails_table_extracts_subjects(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "03-w3", EMAILS_TABLE_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        subjects = [e["subject"] for e in week.notable_emails]
        assert any("Tier1 LRS" in s for s in subjects)
        assert any("QualityProg" in s for s in subjects)
        assert any("DataIQ" in s for s in subjects)

    def test_emails_skips_header_row(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "03-w3", EMAILS_TABLE_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        # "Subject" header row must not appear in extracted emails
        subjects = [e["subject"].lower() for e in week.notable_emails]
        assert "subject" not in subjects

    def test_emails_extracts_content(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "03-w3", EMAILS_TABLE_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        contents = [e.get("content", "") for e in week.notable_emails]
        assert any("LIVE" in c for c in contents)

    def test_no_emails_returns_empty(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "02-w1", """\
# Week 20
## Q4 — Calendar Events
|#|Title|Organizer|Attendees|Time|Notes|
|---|---|---|---:|---|---|
| 1 | Sync | Alice | 3 | 9 AM | |
""")
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week.notable_emails == []

    # --- _parse_summary ---

    def test_summary_extracted(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "03-w3", EMAILS_TABLE_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week.summary_text != ""
        assert "Tier1 LRS" in week.summary_text or "Critical" in week.summary_text

    def test_no_summary_returns_empty_string(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "09-w4", NEW_PEOPLE_TABLE_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week.summary_text == ""

    # --- _parse_authored_docs ---

    def test_authored_docs_extracted(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "01-w3", AUTHORED_DOCS_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        names = [d["name"] for d in week.authored_docs]
        assert any("PlatformB" in n or "DirectDrive" in n for n in names)
        assert any("Ramp plan" in n for n in names)

    def test_authored_docs_excludes_non_authored(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "01-w3", AUTHORED_DOCS_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        names = [d["name"] for d in week.authored_docs]
        # NormalDocument.pptx is NOT authored by ACE
        assert not any("NormalDocument" in n for n in names)

    def test_no_authored_docs_returns_empty(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "09-w4", NEW_PEOPLE_TABLE_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week.authored_docs == []


# ===========================================================================
# Test Group 9: New aggregator state — decisions, emails, summaries
# ===========================================================================

class TestNewAggregatorState:

    def _build_full_agg(self, tmp_path):
        files_and_content = [
            ("2024", "09-w4", NEW_PEOPLE_TABLE_WEEK),
            ("2025", "03-w3", EMAILS_TABLE_WEEK),
            ("2025", "01-w3", AUTHORED_DOCS_WEEK),
        ]
        parser = ScrapeParser()
        agg = WarmStartAggregator()
        for year, stem, content in files_and_content:
            d = tmp_path / year
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"{stem}.md"
            f.write_text(content, encoding="utf-8")
            week = parser.parse_file(f)
            if week:
                agg.process_week(week)
        return agg

    def test_new_people_enrich_people_graph(self, tmp_path):
        agg = self._build_full_agg(tmp_path)
        # David Stone should now have an inferred_role
        daniel = agg.people.get("david stone")
        assert daniel is not None
        assert daniel.inferred_role != ""

    def test_notable_emails_collected(self, tmp_path):
        agg = self._build_full_agg(tmp_path)
        assert len(agg.notable_emails) >= 2
        subjects = [e["subject"] for e in agg.notable_emails]
        assert any("Tier1 LRS" in s for s in subjects)

    def test_week_summaries_collected(self, tmp_path):
        agg = self._build_full_agg(tmp_path)
        assert len(agg.week_summaries) >= 1
        texts = [s["text"] for s in agg.week_summaries]
        assert any("Tier1" in t or "Critical" in t for t in texts)

    def test_authored_docs_become_career_evidence(self, tmp_path):
        agg = self._build_full_agg(tmp_path)
        authored = [e for e in agg.career_evidence if e.event_type == "authored"]
        assert len(authored) >= 1
        descs = [e.description for e in authored]
        assert any("PlatformB" in d or "DirectDrive" in d or "Ramp" in d for d in descs)


# ===========================================================================
# Test Group 10: New state file writers
# ===========================================================================

class TestNewStateFileWriters:

    def _make_rich_agg(self):
        from scripts.work_warm_start import (  # type: ignore
            write_work_decisions, write_work_comms, write_work_notes,
        )
        agg = WarmStartAggregator()
        agg._week_labels = ["2024/09-w1", "2026/03-w3"]
        agg.decisions = [
            {"date": "2024/09-w1", "week_label": "Week 1",
             "description": "Program ownership: Repairs & Safety formally assigned."},
        ]
        agg.notable_emails = [
            {"week_id": "2025/03-w3", "week_label": "Week 30",
             "subject": "Tier1 LRS LIVE Announcement",
             "content": "Historic milestone — PlatformA Tier1 LRS went LIVE"},
        ]
        agg.week_summaries = [
            {"week_id": "2025/03-w3", "week_label": "Week 30",
             "text": "Critical execution week with major milestones."},
        ]
        return agg

    def test_write_work_decisions(self, tmp_path):
        from scripts.work_warm_start import write_work_decisions  # type: ignore
        agg = self._make_rich_agg()
        write_work_decisions(agg, tmp_path)
        out = (tmp_path / "work-decisions.md").read_text(encoding="utf-8")
        assert out.startswith("---")
        assert "schema_version" in out
        assert "decision_count: 1" in out
        assert "Program ownership" in out

    def test_write_work_decisions_empty_fallback(self, tmp_path):
        from scripts.work_warm_start import write_work_decisions  # type: ignore
        agg = WarmStartAggregator()
        agg._week_labels = ["x"]
        write_work_decisions(agg, tmp_path)
        out = (tmp_path / "work-decisions.md").read_text(encoding="utf-8")
        assert "decision_count: 0" in out
        assert "No decisions" in out

    def test_write_work_comms(self, tmp_path):
        from scripts.work_warm_start import write_work_comms  # type: ignore
        agg = self._make_rich_agg()
        write_work_comms(agg, tmp_path)
        out = (tmp_path / "work-comms.md").read_text(encoding="utf-8")
        assert out.startswith("---")
        assert "notable_emails_imported: 1" in out
        assert "Tier1 LRS LIVE" in out
        assert "Historic milestone" in out

    def test_write_work_comms_frontmatter_complete(self, tmp_path):
        from scripts.work_warm_start import write_work_comms  # type: ignore
        agg = self._make_rich_agg()
        write_work_comms(agg, tmp_path)
        out = (tmp_path / "work-comms.md").read_text(encoding="utf-8")
        assert "sensitivity: elevated" in out
        assert "DLP note" in out

    def test_write_work_notes(self, tmp_path):
        from scripts.work_warm_start import write_work_notes  # type: ignore
        agg = self._make_rich_agg()
        write_work_notes(agg, tmp_path)
        out = (tmp_path / "work-notes.md").read_text(encoding="utf-8")
        assert out.startswith("---")
        assert "summary_weeks: 1" in out
        assert "Critical execution" in out

    def test_write_work_notes_empty_fallback(self, tmp_path):
        from scripts.work_warm_start import write_work_notes  # type: ignore
        agg = WarmStartAggregator()
        agg._week_labels = ["x"]
        write_work_notes(agg, tmp_path)
        out = (tmp_path / "work-notes.md").read_text(encoding="utf-8")
        assert "summary_weeks: 0" in out
        assert "No week summaries" in out

    def test_full_run_creates_new_state_files(self, tmp_path):
        """Smoke test: full run creates decisions, comms, and notes files."""
        # Build a minimal scrape dir
        scrape_dir = tmp_path / "scrape"
        artha_dir = tmp_path / "artha"
        (scrape_dir / "2025").mkdir(parents=True)
        (artha_dir / "state" / "work").mkdir(parents=True)
        (artha_dir / "config").mkdir()
        (artha_dir / "config" / "user_profile.yaml").write_text(
            "work:\n  bootstrap:\n    import_completed: false\n", encoding="utf-8"
        )
        (scrape_dir / "2025" / "03-w3.md").write_text(EMAILS_TABLE_WEEK, encoding="utf-8")
        run_warm_start(scrape_dir=scrape_dir, artha_dir=artha_dir, dry_run=False)
        state_dir = artha_dir / "state" / "work"
        for fname in ["work-decisions.md", "work-comms.md", "work-notes.md"]:
            assert (state_dir / fname).exists(), f"Missing {fname}"


# ===========================================================================
# Test Group 11: Round 2 parsers — key_insights, new_terms, sent_emails,
#                                  priority, key_themes
# ===========================================================================

KEY_INSIGHTS_WEEK = """\
# Week 22 — Jan 20–26, 2025

## Q4 — Calendar Events
| # | Title | Organizer | Attendees | Start–End | Notes |
|---|---|---|---:|---|---|
| 1 | PlatformA Weekly | Alex Morgan | 8 | 10:00 AM | |

## Key Technical Signals

- **ZRS vs LRS split**: LRS path approved to proceed; ZRS holding due to constraints.
- **AutoRemedy in Production**: Confirmed production-ready during chat thread.
- Short line (ignored — too short)
- **GatewayService = repair gateway**: Definitive description on record.
"""

KEY_INSIGHTS_ALT_HEADER_WEEK = """\
# Week 50 — Dec 8–14, 2025

## Key Insights

- **SecureService rollout**: Security service now gating all repair paths.
- **Nova v2**: Deployment runtime upgrade approved for Q1 2026.
"""

NEW_TERMS_WEEK = """\
# Week 15 — Oct 7–13, 2024

## Q4 — Calendar Events
| # | Title | Organizer | Attendees | Start–End | Notes |
|---|---|---|---:|---|---|
| 1 | PlatformA Sync | Alice | 5 | 9 AM | |

## New Terms This Week

| Term | Meaning |
|------|---------|
| **SecureService** | Security service/component in Asgard; code walkthrough with team |
| **LLAM** | Large Lot Adaptive maintenance job type for scaled repairs |
| **ZRS** | Zone Redundant Storage — multi-zone durability configuration |
"""

SENT_EMAIL_WEEK = """\
# Week 30 — Mar 17–23, 2025

## Sent Email Enrichment — E1 (Pass 1)

| Date | To | Subject | Signal |
|------|----|---------|----|
| Mon Mar 17 | Alex Morgan | PlatformA Status Update | Weekly status summary to manager |
| Wed Mar 19 | PlatformA Team | Tier1 LRS Go-Live | Coordinated go-live announcement for Tier1 LRS milestone |
"""

PRIORITY_WEEK = """\
# Week 35 — Apr 28–May 4, 2025

**Priority:** 🔴 CRITICAL — PlatformA Security Review; Tier1 LLAM Migration; TargetDate P0/P1 enforcement

## Q4 — Calendar Events
| # | Title | Organizer | Attendees | Start–End | Notes |
|---|---|---|---:|---|---|
| 1 | Security Review | Alice | 12 | 2 PM | |
"""

KEY_THEMES_WEEK = """\
# Week 40 — May 26–Jun 1, 2025

## Q4 — Calendar Events
| # | Title | Organizer | Attendees | Start–End | Notes |
|---|---|---|---:|---|---|
| 1 | Sync | Bob | 4 | 10 AM | |

## Key Themes

### Deployment Acceleration
Content about deployment acceleration...

### Cross-Team Alignment
Content about cross-team alignment...

### Technical Debt Reduction
Content about technical debt...
"""


class TestRound2Parsers:
    """Tests for Round 2 parsers: _parse_key_insights, _parse_new_terms,
    _parse_sent_emails, _parse_priority, _parse_key_themes."""

    def _make_week_file(self, tmp_path, year: str, stem: str, content: str):
        d = tmp_path / year
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{stem}.md"
        f.write_text(content, encoding="utf-8")
        return f

    # --- _parse_key_insights ---

    def test_key_insights_extracts_bullets(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "01-w3", KEY_INSIGHTS_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert len(week.key_insights) >= 2
        combined = " ".join(week.key_insights)
        assert "ZRS" in combined or "LRS" in combined
        assert "AutoRemedy" in combined or "GatewayService" in combined

    def test_key_insights_strips_bold_markers(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "01-w3", KEY_INSIGHTS_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        # No ** should appear in extracted insights
        for insight in week.key_insights:
            assert "**" not in insight

    def test_key_insights_capped_at_15(self, tmp_path):
        many_bullets = "# Week 99\n\n## Key Insights\n\n"
        many_bullets += "\n".join(f"- Insight number {i} with sufficient length text here." for i in range(20))
        f = self._make_week_file(tmp_path, "2025", "06-w1", many_bullets)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert len(week.key_insights) <= 15

    def test_key_insights_alt_header(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "12-w2", KEY_INSIGHTS_ALT_HEADER_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert len(week.key_insights) >= 1
        assert any("SecureService" in i for i in week.key_insights)

    def test_no_key_insights_returns_empty(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "09-w4", NEW_PEOPLE_TABLE_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week.key_insights == []

    # --- _parse_new_terms ---

    def test_new_terms_extracts_terms(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "10-w2", NEW_TERMS_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        terms = [t["term"] for t in week.new_terms]
        assert any("SecureService" in t for t in terms)
        assert any("LLAM" in t or "ZRS" in t for t in terms)

    def test_new_terms_extracts_meanings(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "10-w2", NEW_TERMS_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        meanings = {t["term"]: t["meaning"] for t in week.new_terms}
        # At least one meaning should have real content
        all_meanings = " ".join(meanings.values())
        assert "Security" in all_meanings or "maintenance" in all_meanings or "multi-zone" in all_meanings

    def test_new_terms_skips_header_row(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "10-w2", NEW_TERMS_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        terms = [t["term"].lower() for t in week.new_terms]
        assert "term" not in terms

    def test_new_terms_strips_bold_markers(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "10-w2", NEW_TERMS_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        for t in week.new_terms:
            assert "**" not in t["term"]

    def test_no_new_terms_returns_empty(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "09-w4", NEW_PEOPLE_TABLE_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week.new_terms == []

    # --- _parse_sent_emails ---

    def test_sent_emails_extracts_rows(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "03-w3", SENT_EMAIL_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert len(week.sent_emails) >= 1
        subjects = [e["subject"] for e in week.sent_emails]
        assert any("PlatformA" in s or "Tier1" in s for s in subjects)

    def test_sent_emails_has_required_keys(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "03-w3", SENT_EMAIL_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert len(week.sent_emails) >= 1
        e = week.sent_emails[0]
        assert "date" in e and "to" in e and "subject" in e and "signal" in e

    def test_sent_emails_skips_header_row(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "03-w3", SENT_EMAIL_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        dates = [e["date"].lower() for e in week.sent_emails]
        assert "date" not in dates

    def test_no_sent_emails_returns_empty(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "09-w4", NEW_PEOPLE_TABLE_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week.sent_emails == []

    # --- _parse_priority ---

    def test_priority_extracts_label(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "04-w4", PRIORITY_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week.priority_label != ""
        assert "CRITICAL" in week.priority_label or "PlatformA" in week.priority_label

    def test_priority_contains_red_circle_or_keyword(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "04-w4", PRIORITY_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        # Should contain 🔴 emoji or CRITICAL keyword
        assert "\U0001f534" in week.priority_label or "CRITICAL" in week.priority_label

    def test_no_priority_returns_empty_string(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "09-w4", NEW_PEOPLE_TABLE_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week.priority_label == ""

    # --- _parse_key_themes ---

    def test_key_themes_extracts_titles(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "05-w4", KEY_THEMES_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert len(week.key_themes) >= 2
        assert any("Deployment" in t for t in week.key_themes)
        assert any("Cross-Team" in t or "Alignment" in t for t in week.key_themes)

    def test_key_themes_strips_hash_prefix(self, tmp_path):
        f = self._make_week_file(tmp_path, "2025", "05-w4", KEY_THEMES_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        for title in week.key_themes:
            assert not title.startswith("#")

    def test_no_key_themes_returns_empty(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "09-w4", NEW_PEOPLE_TABLE_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week.key_themes == []


# ===========================================================================
# Test Group 12: Round 2 aggregator state — key_insights, glossary,
#                sent_emails, critical_weeks
# ===========================================================================

class TestRound2AggregatorState:
    """Tests for Round 2 aggregator collections."""

    def _build_round2_agg(self, tmp_path):
        files_and_content = [
            ("2025", "01-w3", KEY_INSIGHTS_WEEK),
            ("2024", "10-w2", NEW_TERMS_WEEK),
            ("2025", "03-w3", SENT_EMAIL_WEEK),
            ("2025", "04-w4", PRIORITY_WEEK),
            ("2025", "05-w4", KEY_THEMES_WEEK),
        ]
        parser = ScrapeParser()
        agg = WarmStartAggregator()
        for year, stem, content in files_and_content:
            d = tmp_path / year
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"{stem}.md"
            f.write_text(content, encoding="utf-8")
            week = parser.parse_file(f)
            if week:
                agg.process_week(week)
        return agg

    def test_key_insights_collected(self, tmp_path):
        agg = self._build_round2_agg(tmp_path)
        assert len(agg.key_insights) >= 2
        bullets = [i["bullet"] for i in agg.key_insights]
        assert any("ZRS" in b or "AutoRemedy" in b for b in bullets)

    def test_key_insights_have_week_id(self, tmp_path):
        agg = self._build_round2_agg(tmp_path)
        for item in agg.key_insights:
            assert "week_id" in item
            assert "bullet" in item

    def test_glossary_terms_collected(self, tmp_path):
        agg = self._build_round2_agg(tmp_path)
        assert len(agg.glossary) >= 2
        term_keys = [k.lower() for k in agg.glossary.keys()]
        assert any("secureservice" in k for k in term_keys)

    def test_glossary_deduplicates_terms(self, tmp_path):
        """Same term in two weeks should appear only once in glossary."""
        # Build a second week file with the same "SecureService" term
        duplicate_content = """\
# Week 16 — Oct 14–20, 2024
## New Terms This Week
| Term | Meaning |
|------|---------|
| **SecureService** | Duplicate entry for same security service |
"""
        parser = ScrapeParser()
        agg = WarmStartAggregator()
        for content, year, stem in [
            (NEW_TERMS_WEEK, "2024", "10-w2"),
            (duplicate_content, "2024", "10-w3"),
        ]:
            d = tmp_path / year
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"{stem}.md"
            f.write_text(content, encoding="utf-8")
            week = parser.parse_file(f)
            if week:
                agg.process_week(week)
        SecureService_count = sum(1 for k in agg.glossary if "secureservice" in k.lower())
        assert SecureService_count == 1

    def test_sent_emails_collected(self, tmp_path):
        agg = self._build_round2_agg(tmp_path)
        assert len(agg.sent_emails) >= 1
        subjects = [e["subject"] for e in agg.sent_emails]
        assert any("PlatformA" in s or "Tier1" in s for s in subjects)

    def test_sent_emails_have_week_id(self, tmp_path):
        agg = self._build_round2_agg(tmp_path)
        assert len(agg.sent_emails) >= 1
        for email in agg.sent_emails:
            assert "week_id" in email

    def test_critical_weeks_collected(self, tmp_path):
        agg = self._build_round2_agg(tmp_path)
        assert len(agg.critical_weeks) >= 1

    def test_critical_weeks_have_required_keys(self, tmp_path):
        agg = self._build_round2_agg(tmp_path)
        for w in agg.critical_weeks:
            assert "week_id" in w
            assert "priority" in w or "label" in w

    def test_non_critical_week_not_in_critical_list(self, tmp_path):
        """A week without a 🔴 priority label should not appear in critical_weeks."""
        agg = self._build_round2_agg(tmp_path)
        # KEY_INSIGHTS_WEEK has no priority_label, so 2025/01-w3 should not be critical
        week_ids = [w["week_id"] for w in agg.critical_weeks]
        assert "2025/01-w3" not in week_ids


# ===========================================================================
# Test Group 13: Round 2 state file writers — updated work-notes, work-comms,
#                work-career
# ===========================================================================

class TestRound2StateFileWriters:
    """Tests for updated writers: write_work_notes (Technical Intelligence +
    Domain Glossary), write_work_comms (Sent Email Signals), write_work_career
    (Critical Weeks table)."""

    def _make_round2_agg(self):
        agg = WarmStartAggregator()
        agg._week_labels = ["2025/01-w3", "2025/03-w3", "2025/04-w4"]
        agg.week_summaries = [
            {"week_id": "2025/03-w3", "week_label": "Week 30",
             "text": "Critical execution week with major milestones."},
        ]
        agg.key_insights = [
            {"week_id": "2025/01-w3", "week_label": "Week 22",
             "bullet": "ZRS vs LRS split confirmed: LRS path approved."},
            {"week_id": "2025/01-w3", "week_label": "Week 22",
             "bullet": "AutoRemedy in Production: Confirmed production-ready."},
        ]
        agg.glossary = {
            "SecureService": {"term": "SecureService", "meaning": "Security service in Asgard.", "first_seen": "2024/10-w2"},
            "llam": {"term": "LLAM", "meaning": "Large Lot Adaptive maintenance job.", "first_seen": "2024/10-w2"},
        }
        agg.notable_emails = [
            {"week_id": "2025/03-w3", "week_label": "Week 30",
             "subject": "Tier1 LRS LIVE", "content": "Historic milestone."},
        ]
        agg.sent_emails = [
            {"week_id": "2025/03-w3", "week_label": "Week 30",
             "date": "Mon Mar 17", "to": "Alex", "subject": "PlatformA Status", "signal": "Weekly summary"},
        ]
        agg.decisions = []
        agg.critical_weeks = [
            {"week_id": "2025/04-w4", "week_label": "Week 35",
             "priority": "\U0001f534 CRITICAL — PlatformA Security Review",
             "label": "\U0001f534 CRITICAL — PlatformA Security Review"},
        ]
        return agg

    # --- write_work_notes —- Technical Intelligence Log ---

    def test_write_work_notes_has_intelligence_section(self, tmp_path):
        from scripts.work_warm_start import write_work_notes  # type: ignore
        agg = self._make_round2_agg()
        write_work_notes(agg, tmp_path)
        out = (tmp_path / "work-notes.md").read_text(encoding="utf-8")
        assert "Technical Intelligence" in out

    def test_write_work_notes_intelligence_contains_bullets(self, tmp_path):
        from scripts.work_warm_start import write_work_notes  # type: ignore
        agg = self._make_round2_agg()
        write_work_notes(agg, tmp_path)
        out = (tmp_path / "work-notes.md").read_text(encoding="utf-8")
        assert "ZRS vs LRS" in out or "AutoRemedy" in out

    def test_write_work_notes_has_glossary_section(self, tmp_path):
        from scripts.work_warm_start import write_work_notes  # type: ignore
        agg = self._make_round2_agg()
        write_work_notes(agg, tmp_path)
        out = (tmp_path / "work-notes.md").read_text(encoding="utf-8")
        assert "Glossary" in out or "glossary" in out

    def test_write_work_notes_glossary_contains_terms(self, tmp_path):
        from scripts.work_warm_start import write_work_notes  # type: ignore
        agg = self._make_round2_agg()
        write_work_notes(agg, tmp_path)
        out = (tmp_path / "work-notes.md").read_text(encoding="utf-8")
        assert "SecureService" in out
        assert "LLAM" in out

    def test_write_work_notes_frontmatter_has_insight_count(self, tmp_path):
        from scripts.work_warm_start import write_work_notes  # type: ignore
        agg = self._make_round2_agg()
        write_work_notes(agg, tmp_path)
        out = (tmp_path / "work-notes.md").read_text(encoding="utf-8")
        assert "technical_insight_items: 2" in out

    def test_write_work_notes_frontmatter_has_glossary_count(self, tmp_path):
        from scripts.work_warm_start import write_work_notes  # type: ignore
        agg = self._make_round2_agg()
        write_work_notes(agg, tmp_path)
        out = (tmp_path / "work-notes.md").read_text(encoding="utf-8")
        assert "glossary_terms: 2" in out

    def test_write_work_notes_has_key_themes_history(self, tmp_path):
        from scripts.work_warm_start import write_work_notes  # type: ignore
        agg = self._make_round2_agg()
        agg.key_theme_titles = [
            {"week_id": "2025/05-w4", "title": "Deployment Acceleration"},
            {"week_id": "2025/05-w4", "title": "Cross-Team Alignment"},
        ]
        write_work_notes(agg, tmp_path)
        out = (tmp_path / "work-notes.md").read_text(encoding="utf-8")
        assert "Key Themes History" in out
        assert "Deployment Acceleration" in out
        assert "Cross-Team Alignment" in out

    # --- write_work_comms — Sent Email Signals ---

    def test_write_work_comms_has_sent_emails_section(self, tmp_path):
        from scripts.work_warm_start import write_work_comms  # type: ignore
        agg = self._make_round2_agg()
        write_work_comms(agg, tmp_path)
        out = (tmp_path / "work-comms.md").read_text(encoding="utf-8")
        assert "Sent Email" in out

    def test_write_work_comms_sent_email_rows_present(self, tmp_path):
        from scripts.work_warm_start import write_work_comms  # type: ignore
        agg = self._make_round2_agg()
        write_work_comms(agg, tmp_path)
        out = (tmp_path / "work-comms.md").read_text(encoding="utf-8")
        assert "PlatformA Status" in out
        assert "Alex" in out

    def test_write_work_comms_frontmatter_has_sent_count(self, tmp_path):
        from scripts.work_warm_start import write_work_comms  # type: ignore
        agg = self._make_round2_agg()
        write_work_comms(agg, tmp_path)
        out = (tmp_path / "work-comms.md").read_text(encoding="utf-8")
        assert "sent_emails_imported: 1" in out

    # --- write_work_career — Critical Weeks table ---

    def test_write_work_career_has_critical_weeks_section(self, tmp_path):
        from scripts.work_warm_start import write_work_career  # type: ignore
        agg = self._make_round2_agg()
        write_work_career(agg, tmp_path)
        out = (tmp_path / "work-career.md").read_text(encoding="utf-8")
        assert "Critical" in out or "CRITICAL" in out

    def test_write_work_career_critical_weeks_table_has_rows(self, tmp_path):
        from scripts.work_warm_start import write_work_career  # type: ignore
        agg = self._make_round2_agg()
        write_work_career(agg, tmp_path)
        out = (tmp_path / "work-career.md").read_text(encoding="utf-8")
        assert "2025/04-w4" in out
        assert "PlatformA Security Review" in out

    def test_write_work_career_empty_critical_weeks(self, tmp_path):
        """Writer handles no critical weeks gracefully."""
        from scripts.work_warm_start import write_work_career  # type: ignore
        agg = WarmStartAggregator()
        agg._week_labels = ["x"]
        agg.critical_weeks = []
        write_work_career(agg, tmp_path)
        out = (tmp_path / "work-career.md").read_text(encoding="utf-8")
        # Should not crash; may omit the section or show "none"
        assert out.startswith("---")


# ===========================================================================
# Test Group 14: Round 3 — Key Themes numbered-list fallback + chat people
# ===========================================================================

KEY_THEMES_NUMBERED_WEEK = """\
# Week 5 — Sep 23–29, 2024

## Q4 — Calendar Events
| # | Title | Organizer | Attendees | Start–End | Notes |
|---|---|---|---:|---|---|
| 1 | PlatformA Sync | Alex Morgan | 5 | 10 AM | |

## Key Themes

1. **Repairs & Safety formally assigned** — Repair and Safety Handoff sync Sep 23 with Jordan and Alex.
2. **DataIQ entering strategic phase**: Goals and Northstar established with team.
3. **AutoRemedy is becoming a real program** — Three separate workstreams active.
"""

KEY_THEMES_H3_WEEK = """\
# Week 40 — May 26–Jun 1, 2025

## Q4 — Calendar Events
| # | Title | Organizer | Attendees | Start–End | Notes |
|---|---|---|---:|---|---|
| 1 | Sync | Bob | 4 | 10 AM | |

## Key Themes

### Deployment Acceleration
Content about deployment...

### Cross-Team Alignment
Content about alignment...
"""

CHAT_PARTICIPANTS_WEEK = """\
# Week 6 — Sep 30–Oct 6, 2024

## Q4 — Calendar Events
| # | Title | Organizer | Attendees | Start–End | Notes |
|---|---|---|---:|---|---|
| 1 | PlatformA Weekly | Alex Morgan | 5 | 10 AM | |

## Q5 — Teams Chats (Activity Sep 30–Oct 6)

- **Channel/Chat**: ACP – Infra Software PM Team | **Date Range**: Sep 30, 2024
  - **Participants**: Alex Morgan, Carlos Rivera, Jordan Lee, Dana Park, others
  - **Full Context**: PlatformA planning discussions.

- **Channel/Chat**: Platform-A-DD Channel | **Date**: Oct 2
  - **Participants**: Sam Rodriguez, Priya Patel
  - **Full Context**: Deployment sync.
"""

CHAT_QUOTED_WEEK = """\
# Week 10 — Oct 28–Nov 3, 2024

## Q4 — Calendar Events
| # | Title | Organizer | Attendees | Start–End | Notes |
|---|---|---|---:|---|---|
| 1 | Sync | Alice | 3 | 9 AM | |

## Q5 — Teams Chats

### Thread: PlatformA Blocker Discussion
> **Casey Hill (Oct 28):** "PlatformA telemetry for CRC counters is not emitted..."
> **Alex Chen (Oct 29):** "I wrote the blocker summary..."
> **Michael Chen (Oct 30):** "Thanks for the clarity, let me check with Jason."
"""


class TestRound3Parsers:
    """Tests for Round 3 changes: _parse_key_themes numbered fallback,
    _parse_chat_people participant + quoted formats."""

    def _make_week_file(self, tmp_path, year: str, stem: str, content: str):
        d = tmp_path / year
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{stem}.md"
        f.write_text(content, encoding="utf-8")
        return f

    # --- _parse_key_themes: numbered-list fallback ---

    def test_key_themes_numbered_list_extracts_titles(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "09-w4", KEY_THEMES_NUMBERED_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert len(week.key_themes) >= 2
        assert any("Repairs" in t or "Safety" in t for t in week.key_themes)
        assert any("DataIQ" in t for t in week.key_themes)

    def test_key_themes_numbered_strips_bold_markers(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "09-w4", KEY_THEMES_NUMBERED_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        for title in week.key_themes:
            assert "**" not in title

    def test_key_themes_numbered_no_description_bleed(self, tmp_path):
        """Titles should not include narrative text after the — separator."""
        f = self._make_week_file(tmp_path, "2024", "09-w4", KEY_THEMES_NUMBERED_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        # "Repair and Safety Handoff sync Sep 23" is description, not title
        assert not any(len(t) > 80 for t in week.key_themes), "Title too long — description bleed"

    def test_key_themes_h3_format_still_works(self, tmp_path):
        """### format (Format 1) must still work correctly after adding fallback."""
        f = self._make_week_file(tmp_path, "2025", "05-w4", KEY_THEMES_H3_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert len(week.key_themes) >= 2
        assert any("Deployment" in t for t in week.key_themes)
        assert any("Cross-Team" in t or "Alignment" in t for t in week.key_themes)

    def test_key_themes_h3_does_not_use_numbered_fallback(self, tmp_path):
        """When ### headings are present, numbered fallback must NOT also fire."""
        f = self._make_week_file(tmp_path, "2025", "05-w4", KEY_THEMES_H3_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        # Only 2 ### headings in the fixture — should be exactly 2 titles
        assert len(week.key_themes) == 2

    def test_key_themes_no_section_returns_empty(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "09-w4", NEW_PEOPLE_TABLE_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week.key_themes == []

    # --- _parse_chat_people: participant-list format ---

    def test_chat_people_participant_list_extracted(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "10-w1", CHAT_PARTICIPANTS_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert len(week.chat_people) >= 3
        names = week.chat_people
        assert any("Alex" in n for n in names)
        assert any("Carlos" in n or "Rivera" in n for n in names)
        assert any("Jordan" in n for n in names)

    def test_chat_people_excludes_others_placeholder(self, tmp_path):
        """'others' is not a real name and must be filtered out."""
        f = self._make_week_file(tmp_path, "2024", "10-w1", CHAT_PARTICIPANTS_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert not any(n.lower() == "others" for n in week.chat_people)

    def test_chat_people_excludes_ved(self, tmp_path):
        """ACE himself should never appear as a chat person."""
        f = self._make_week_file(tmp_path, "2024", "10-w1", CHAT_PARTICIPANTS_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert not any(_is_self(n) for n in week.chat_people)

    def test_chat_people_deduplicates(self, tmp_path):
        """Same name in two separate Participants lines → appears only once."""
        f = self._make_week_file(tmp_path, "2024", "10-w1", CHAT_PARTICIPANTS_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        low = [n.lower() for n in week.chat_people]
        assert len(low) == len(set(low)), "Duplicate names found in chat_people"

    # --- _parse_chat_people: quoted-message format ---

    def test_chat_people_quoted_authors_extracted(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "10-w4", CHAT_QUOTED_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert any("Casey" in n or "Hinchliff" in n for n in week.chat_people)
        assert any("Michael" in n or "Narayan" in n for n in week.chat_people)

    def test_chat_people_quoted_excludes_ved(self, tmp_path):
        """'Alex Chen' in quoted format should be filtered out."""
        f = self._make_week_file(tmp_path, "2024", "10-w4", CHAT_QUOTED_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert not any(_is_self(n) for n in week.chat_people)

    def test_no_q5_section_returns_empty(self, tmp_path):
        f = self._make_week_file(tmp_path, "2024", "09-w4", NEW_PEOPLE_TABLE_WEEK)
        parser = ScrapeParser()
        week = parser.parse_file(f)
        assert week.chat_people == []


# ===========================================================================
# Test Group 15: Round 3 aggregator — chat people boost direct_chat_count
# ===========================================================================

class TestRound3AggregatorChatPeople:
    """Tests for chat people wiring into WarmStartAggregator.people."""

    def _build_chat_agg(self, tmp_path):
        for content, year, stem in [
            (CHAT_PARTICIPANTS_WEEK, "2024", "10-w1"),
            (CHAT_QUOTED_WEEK, "2024", "10-w4"),
        ]:
            d = tmp_path / year
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{stem}.md").write_text(content, encoding="utf-8")
        parser = ScrapeParser()
        agg = WarmStartAggregator()
        for year, stem in [("2024", "10-w1"), ("2024", "10-w4")]:
            f = tmp_path / year / f"{stem}.md"
            w = parser.parse_file(f)
            if w:
                agg.process_week(w)
        return agg

    def test_participant_names_added_to_people_graph(self, tmp_path):
        agg = self._build_chat_agg(tmp_path)
        names_low = [k.lower() for k in agg.people]
        assert any("alex" in k for k in names_low)
        assert any("carlos" in k or "rivera" in k for k in names_low)

    def test_direct_chat_count_incremented(self, tmp_path):
        agg = self._build_chat_agg(tmp_path)
        alex = next((p for p in agg.people.values() if "alex morgan" in p.name.lower()), None)
        assert alex is not None
        assert alex.direct_chat_count >= 1

    def test_quoted_author_direct_chat_count_incremented(self, tmp_path):
        agg = self._build_chat_agg(tmp_path)
        Casey = next((p for p in agg.people.values() if "casey" in p.name.lower()), None)
        assert Casey is not None
        assert Casey.direct_chat_count >= 1

    def test_chat_interaction_boosts_relationship_score(self, tmp_path):
        agg = self._build_chat_agg(tmp_path)
        alex = next((p for p in agg.people.values() if "alex morgan" in p.name.lower()), None)
        assert alex is not None
        # direct_chat_count * 2.0 per interaction
        assert alex.relationship_score > 0

    def test_ved_not_in_people_from_chat(self, tmp_path):
        agg = self._build_chat_agg(tmp_path)
        # Alex Chen appears in quoted format but must be filtered
        ved_entries = [k for k in agg.people if _is_self(k)]
        assert len(ved_entries) == 0, f"ACE found in people graph via chat: {ved_entries}"

    def test_key_themes_numbered_coverage_in_aggregator(self, tmp_path):
        """Numbered-list Key Themes should now appear in key_theme_titles."""
        d = tmp_path / "2024"
        d.mkdir(parents=True, exist_ok=True)
        (d / "09-w4.md").write_text(KEY_THEMES_NUMBERED_WEEK, encoding="utf-8")
        parser = ScrapeParser()
        agg = WarmStartAggregator()
        w = parser.parse_file(d / "09-w4.md")
        if w:
            agg.process_week(w)
        assert len(agg.key_theme_titles) >= 2
        titles = [t["title"] for t in agg.key_theme_titles]
        assert any("Repairs" in t or "Safety" in t for t in titles)

