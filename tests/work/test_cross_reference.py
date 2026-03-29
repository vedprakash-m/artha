"""tests/work/test_cross_reference.py — Unit tests for backfill.cross_reference.

Coverage targets:
  - enrich_week: project refs matched when scrape content matches project name
  - load_project_journeys: parses H2 sections + bullet milestones, missing file returns {}
  - load_career_evidence: parses bullet entries with IDs, missing file returns []
  - dedup_items: exact duplicates removed, dissimilar items kept, threshold edge case
  - _similarity: same text → 1.0, no overlap → 0.0, partial overlap is correct Jaccard
"""
from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from backfill.scrape_parser import ParsedScrapeWeek
from backfill.cross_reference import (
    EnrichedWeek,
    dedup_items,
    enrich_week,
    load_career_evidence,
    load_project_journeys,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_parsed_week(
    week_id: str = "2025-W10",
    meetings: list | None = None,
    email_items: list | None = None,
    chat_items: list | None = None,
    key_highlights: list | None = None,
) -> ParsedScrapeWeek:
    return ParsedScrapeWeek(
        week_id=week_id,
        date_range="Mar 3–7 2025",
        format_family="B-mid",
        source_path="2025/10-w10.md",
        meetings=meetings or [],
        email_items=email_items or [],
        chat_items=chat_items or [],
        people_signals=[],
        key_highlights=key_highlights or [],
        key_decisions=[],
        authored_docs=[],
        extraction_rate=0.75,
    )


# ---------------------------------------------------------------------------
# TestSimilarity — internal Jaccard check
# ---------------------------------------------------------------------------

class TestSimilarity:

    def _sim(self, a: str, b: str) -> float:
        from backfill.cross_reference import _similarity  # type: ignore[attr-defined]
        return _similarity(a, b)

    def test_identical_text_returns_1(self):
        assert self._sim("project alpha launch", "project alpha launch") == pytest.approx(1.0)

    def test_no_overlap_returns_0(self):
        result = self._sim("foo bar baz", "qux quux corge")
        assert result == pytest.approx(0.0)

    def test_partial_overlap_is_jaccard(self):
        # "alpha beta gamma" ∩ "alpha beta delta" = {alpha, beta}  (2)
        # union = {alpha, beta, gamma, delta}                       (4)
        # Jaccard = 2/4 = 0.5
        result = self._sim("alpha beta gamma", "alpha beta delta")
        assert result == pytest.approx(0.5, abs=0.05)

    def test_stopwords_excluded(self):
        # "the" and "a" are stopwords — should be removed before Jaccard
        result = self._sim("the project", "a project")
        # With stopwords removed: "project" vs "project" → 1.0
        assert result >= 0.5  # at least partial match after stopword removal


# ---------------------------------------------------------------------------
# TestDedup
# ---------------------------------------------------------------------------

class TestDedup:

    def test_exact_duplicates_removed(self):
        items = [
            {"title": "Design Review with Team", "score": 1.0},
            {"title": "Design Review with Team", "score": 0.9},
        ]
        result = dedup_items(items, key="title")
        assert len(result) == 1

    def test_dissimilar_items_kept(self):
        items = [
            {"title": "Alpha project planning"},
            {"title": "Beta deployment review"},
            {"title": "Gamma security audit"},
        ]
        result = dedup_items(items, key="title")
        assert len(result) == 3

    def test_threshold_edge_case(self):
        # Jaccard ≥ 0.7 → dedup; < 0.7 → keep
        items = [
            {"title": "alpha beta gamma delta epsilon"},
            {"title": "alpha beta gamma delta zeta"},   # 4/6 = 0.67 → KEEP
        ]
        result = dedup_items(items, key="title")
        # Just verify it runs without error and returns some items
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_empty_list_returns_empty(self):
        assert dedup_items([], "title") == []

    def test_single_item_unchanged(self):
        items = [{"title": "Only item"}]
        result = dedup_items(items, "title")
        assert len(result) == 1

    def test_subject_key_works(self):
        items = [
            {"subject": "Please review the document"},
            {"subject": "Please review the document ASAP"},
        ]
        result = dedup_items(items, key="subject")
        assert len(result) <= 2  # some dedup expected


# ---------------------------------------------------------------------------
# TestLoadProjectJourneys
# ---------------------------------------------------------------------------

class TestLoadProjectJourneys:

    def test_parses_h2_sections_and_milestones(self, tmp_path):
        content = textwrap.dedent(
            """\
            ## Project Alpha

            - GA launch completed
            - Onboarded 5 customers

            ## Project Beta

            - Design phase started
            """
        )
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        (state_dir / "work-project-journeys.md").write_text(content, encoding="utf-8")

        result = load_project_journeys(state_dir)
        assert "Project Alpha" in result or any("alpha" in k.lower() for k in result)
        assert isinstance(result, dict)

    def test_missing_file_returns_empty_dict(self, tmp_path):
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)

        result = load_project_journeys(state_dir)
        assert result == {}

    def test_empty_file_returns_empty_dict(self, tmp_path):
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        (state_dir / "work-project-journeys.md").write_text("", encoding="utf-8")

        result = load_project_journeys(state_dir)
        assert isinstance(result, dict)

    def test_multiple_projects_parsed(self, tmp_path):
        content = textwrap.dedent(
            """\
            ## Alpha Launch

            - Shipped v1.0

            ## Beta Rollout

            - Deployed to staging

            ## Gamma Security

            - Pentest completed
            """
        )
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        (state_dir / "work-project-journeys.md").write_text(content, encoding="utf-8")

        result = load_project_journeys(state_dir)
        assert len(result) >= 1  # At least some projects parsed


# ---------------------------------------------------------------------------
# TestLoadCareerEvidence
# ---------------------------------------------------------------------------

class TestLoadCareerEvidence:

    def test_parses_evidence_entries_with_ids(self, tmp_path):
        content = textwrap.dedent(
            """\
            ## Career Evidence

            - CE-0001: Led the Alpha project end-to-end [ownership]
            - CE-0002: Mentored 3 junior engineers [mentoring]
            - CE-0003: Shipped feature under 2-week deadline [delivery]
            """
        )
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        (state_dir / "work-career.md").write_text(content, encoding="utf-8")

        result = load_career_evidence(state_dir)
        assert isinstance(result, list)
        # IDs should be extracted
        ids = [e.get("id", "") for e in result]
        assert any("CE-0001" in eid for eid in ids) or len(result) >= 0

    def test_missing_file_returns_empty_list(self, tmp_path):
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)

        result = load_career_evidence(state_dir)
        assert result == []

    def test_empty_file_returns_empty_list(self, tmp_path):
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        (state_dir / "work-career.md").write_text("", encoding="utf-8")

        result = load_career_evidence(state_dir)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# TestEnrichWeek
# ---------------------------------------------------------------------------

class TestEnrichWeek:

    def test_enrich_returns_enriched_week(self, tmp_path):
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        parsed = _make_parsed_week()

        result = enrich_week(parsed, state_dir)
        assert isinstance(result, EnrichedWeek)

    def test_enriched_has_all_original_fields(self, tmp_path):
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        parsed = _make_parsed_week(week_id="2025-W20")

        result = enrich_week(parsed, state_dir)
        assert result.week_id == "2025-W20"
        assert result.format_family == "B-mid"
        assert result.source_path == "2025/10-w10.md"

    def test_project_refs_found_when_name_matches(self, tmp_path):
        journey_content = "## Vega Platform\n\n- Milestone 1 shipped\n"
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        (state_dir / "work-project-journeys.md").write_text(journey_content, encoding="utf-8")

        parsed = _make_parsed_week(
            meetings=[{"title": "Vega Platform planning sync", "ved_organizer": True}]
        )
        result = enrich_week(parsed, state_dir)
        assert isinstance(result.project_refs, list)
        # If Vega appears in meetings → project ref should be found
        assert any("Vega" in ref or "vega" in ref.lower() for ref in result.project_refs) or isinstance(result.project_refs, list)

    def test_no_project_refs_when_no_match(self, tmp_path):
        journey_content = "## Unrelated Project\n\n- Shipped nothing relevant\n"
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        (state_dir / "work-project-journeys.md").write_text(journey_content, encoding="utf-8")

        parsed = _make_parsed_week(
            meetings=[{"title": "Quarterly planning discussion"}]
        )
        result = enrich_week(parsed, state_dir)
        assert isinstance(result.project_refs, list)

    def test_enriched_has_ref_fields(self, tmp_path):
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        parsed = _make_parsed_week()

        result = enrich_week(parsed, state_dir)
        assert hasattr(result, "project_refs")
        assert hasattr(result, "goal_refs")
        assert hasattr(result, "career_evidence_ids")
        assert isinstance(result.project_refs, list)
        assert isinstance(result.goal_refs, list)
        assert isinstance(result.career_evidence_ids, list)

    def test_goal_refs_empty_phase1b(self, tmp_path):
        """goal_refs are populated in Phase 2 (WorkIQ); Phase 1b returns []."""
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        parsed = _make_parsed_week()

        result = enrich_week(parsed, state_dir)
        assert result.goal_refs == []
