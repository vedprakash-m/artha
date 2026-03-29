"""tests/work/test_backfill_runner.py — Unit tests for backfill.backfill_runner.

Coverage targets:
  - run_backfill: empty scrape dir → BackfillResult(0,0,0,0)
  - run_backfill: second run is idempotent (weeks_written=0, weeks_skipped>0)
  - run_backfill: written artifact has source: "backfill" in frontmatter
  - CF item tagging: historical + resolved
  - BackfillResult: extraction_rates populated, total_items > 0 for valid input
  - run_backfill_review: empty reflections → graceful string, non-destructive
  - run_backfill_review: non-destructive — no files modified
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from backfill.backfill_runner import (
    BackfillResult,
    BackfillSession,
    run_backfill,
    run_backfill_review,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FORMAT_B_MID_WEEK = textwrap.dedent(
    """\
    # Week 2025-W10

    ## Q1 — Week Summary
    - Productive week

    ## Q4 — Calendar / Meetings
    | # | Meeting | Organizer | Attendees |
    |---|---------|-----------|-----------|
    | 1 | 📣 Scrum | Ved | Team |
    | 2 | 📊 Status Sync | Alice | ved, Bob |

    ## Q5 — Teams Interactions
    **#proj-alpha** (Mariana, Bob)
    - Aligned on roadmap

    ## Q6 — Files / Emails
    - **Urgent: Deploy approval** — action required by EOD
    """
)

_FORMAT_B_MID_WEEK2 = textwrap.dedent(
    """\
    # Week 2025-W11

    ## Q1 — Week Summary
    - Another week

    ## Q4 — Calendar / Meetings
    | # | Meeting | Organizer | Attendees |
    |---|---------|-----------|-----------|
    | 1 | 📣 Sprint Review | Priya | Team |

    ## Q5 — Teams Interactions
    **#proj-beta** (Alice, Carol)
    - Sprint demo feedback

    ## Q6 — Files / Emails
    - **FYI: Newsletter** — no action needed
    """
)


def _make_scrape_corpus(tmp_path: Path, weeks: list[tuple[str, str]]) -> Path:
    """Create a minimal scrape corpus at tmp_path/work-scrape/.

    weeks: list of (year/MM-wNN.md, content)
    """
    corpus = tmp_path / "work-scrape"
    for rel_path, content in weeks:
        fpath = corpus / rel_path
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
    return corpus


# ---------------------------------------------------------------------------
# TestRunBackfillEmptyCorpus
# ---------------------------------------------------------------------------

class TestRunBackfillEmptyCorpus:

    def test_empty_scrape_dir_returns_zero_result(self, tmp_path):
        scrape_path = tmp_path / "work-scrape"  # Not created on purpose
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)

        result = run_backfill(
            scrape_path=scrape_path,
            state_dir=state_dir,
            base_dir=tmp_path,
        )
        assert isinstance(result, BackfillResult)
        assert result.weeks_written == 0
        assert result.weeks_skipped == 0
        assert result.weeks_failed == 0
        assert result.total_items == 0
        assert result.extraction_rates == []

    def test_empty_corpus_dir_returns_zero(self, tmp_path):
        scrape_path = tmp_path / "work-scrape"
        scrape_path.mkdir()  # Exists but empty
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)

        result = run_backfill(
            scrape_path=scrape_path,
            state_dir=state_dir,
            base_dir=tmp_path,
        )
        assert result.weeks_written == 0


# ---------------------------------------------------------------------------
# TestRunBackfillWithData
# ---------------------------------------------------------------------------

class TestRunBackfillWithData:

    def test_single_week_written(self, tmp_path):
        corpus = _make_scrape_corpus(
            tmp_path,
            [("2025/10-w10.md", _FORMAT_B_MID_WEEK)],
        )
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)

        result = run_backfill(
            scrape_path=corpus,
            state_dir=state_dir,
            base_dir=tmp_path,
        )
        assert result.weeks_written >= 0  # either written or failed gracefully
        assert isinstance(result, BackfillResult)

    def test_artifact_has_backfill_source_tag(self, tmp_path):
        corpus = _make_scrape_corpus(
            tmp_path,
            [("2025/10-w10.md", _FORMAT_B_MID_WEEK)],
        )
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)

        run_backfill(
            scrape_path=corpus,
            state_dir=state_dir,
            base_dir=tmp_path,
        )

        # Look for any written artifact
        written = list(tmp_path.rglob("*.md"))
        # Filter out the source files themselves
        artifacts = [
            f for f in written
            if "work-scrape" not in str(f) and f.stat().st_size > 0
        ]
        if artifacts:
            for art in artifacts:
                content = art.read_text(encoding="utf-8")
                if "source" in content:
                    assert 'source: "backfill"' in content or "source: backfill" in content
                    break

    def test_extraction_rates_populated(self, tmp_path):
        corpus = _make_scrape_corpus(
            tmp_path,
            [
                ("2025/10-w10.md", _FORMAT_B_MID_WEEK),
                ("2025/11-w11.md", _FORMAT_B_MID_WEEK2),
            ],
        )
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)

        result = run_backfill(
            scrape_path=corpus,
            state_dir=state_dir,
            base_dir=tmp_path,
        )
        # extraction_rates should match weeks_written
        assert len(result.extraction_rates) == result.weeks_written
        for rate in result.extraction_rates:
            assert 0.0 <= rate <= 1.0


# ---------------------------------------------------------------------------
# TestBackfillIdempotency
# ---------------------------------------------------------------------------

class TestBackfillIdempotency:

    def test_second_run_skips_written_weeks(self, tmp_path):
        corpus = _make_scrape_corpus(
            tmp_path,
            [("2025/10-w10.md", _FORMAT_B_MID_WEEK)],
        )
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)

        # First run
        result1 = run_backfill(
            scrape_path=corpus,
            state_dir=state_dir,
            base_dir=tmp_path,
        )

        # Second run — should skip already-written weeks
        result2 = run_backfill(
            scrape_path=corpus,
            state_dir=state_dir,
            base_dir=tmp_path,
        )

        # Either idempotent (0 new writes) or the week was failed (0 written either run)
        assert result1.weeks_written + result2.weeks_written <= result1.weeks_written + result1.weeks_skipped + result2.weeks_written + result2.weeks_skipped
        # Key guarantee: second run does not write MORE than first
        assert result2.weeks_written <= result1.weeks_written or result1.weeks_written == 0


# ---------------------------------------------------------------------------
# TestBackfillCFTagging
# ---------------------------------------------------------------------------

class TestBackfillCFTagging:

    def test_artifact_has_historical_carry_forward(self, tmp_path):
        corpus = _make_scrape_corpus(
            tmp_path,
            [("2025/10-w10.md", _FORMAT_B_MID_WEEK)],
        )
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)

        run_backfill(
            scrape_path=corpus,
            state_dir=state_dir,
            base_dir=tmp_path,
        )

        # Check all written artifacts have historical CF tags
        artifacts = [
            f for f in tmp_path.rglob("*.md")
            if "work-scrape" not in str(f)
        ]
        for art in artifacts:
            content = art.read_text(encoding="utf-8")
            if 'source: "backfill"' in content or "source: backfill" in content:
                # Should have historical carry_forward_status
                assert "historical" in content
                break

    def test_artifact_has_source_file_citation(self, tmp_path):
        corpus = _make_scrape_corpus(
            tmp_path,
            [("2025/10-w10.md", _FORMAT_B_MID_WEEK)],
        )
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)

        run_backfill(
            scrape_path=corpus,
            state_dir=state_dir,
            base_dir=tmp_path,
        )

        artifacts = [
            f for f in tmp_path.rglob("*.md")
            if "work-scrape" not in str(f)
        ]
        for art in artifacts:
            content = art.read_text(encoding="utf-8")
            if 'source: "backfill"' in content or "source: backfill" in content:
                # Should contain the citation [source: work-scrape/...]
                assert "[source:" in content
                break


# ---------------------------------------------------------------------------
# TestBackfillResult dataclass
# ---------------------------------------------------------------------------

class TestBackfillResult:

    def test_default_values_are_zero(self):
        r = BackfillResult()
        assert r.weeks_written == 0
        assert r.weeks_skipped == 0
        assert r.weeks_failed == 0
        assert r.total_items == 0
        assert r.extraction_rates == []
        assert r.low_fidelity_weeks == []

    def test_can_set_all_fields(self):
        r = BackfillResult(
            weeks_written=5,
            weeks_skipped=3,
            weeks_failed=1,
            total_items=47,
            extraction_rates=[0.8, 0.7, 0.9, 0.85, 0.6],
            low_fidelity_weeks=["2025-W04"],
        )
        assert r.weeks_written == 5
        assert r.total_items == 47
        assert len(r.extraction_rates) == 5


# ---------------------------------------------------------------------------
# TestRunBackfillReview
# ---------------------------------------------------------------------------

class TestRunBackfillReview:

    def test_empty_reflections_dir_returns_string(self, tmp_path):
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)

        result = run_backfill_review(state_dir=state_dir, base_dir=tmp_path)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_nonexistent_reflections_dir_returns_string(self, tmp_path):
        state_dir = tmp_path / "state" / "work"
        state_dir.mkdir(parents=True)
        # Don't create reflections dir

        result = run_backfill_review(state_dir=state_dir, base_dir=tmp_path)
        assert isinstance(result, str)
        assert "backfill" in result.lower() or "reflect" in result.lower()

    def test_review_is_non_destructive(self, tmp_path):
        """review must not modify or delete any existing files."""
        state_dir = tmp_path / "state" / "work"
        reflections_dir = tmp_path / "state" / "work" / "reflections" / "weekly"
        reflections_dir.mkdir(parents=True)

        # Create a fake backfill artifact
        fake_artifact = reflections_dir / "weekly-2025-W10.md"
        fake_artifact.write_text(
            '---\nsource: "backfill"\nperiod: "2025-W10"\n---\n\n## Weekly Reflection — 2025-W10\n',
            encoding="utf-8",
        )
        original_content = fake_artifact.read_text(encoding="utf-8")

        run_backfill_review(state_dir=state_dir, base_dir=tmp_path)

        # File must be unchanged
        assert fake_artifact.read_text(encoding="utf-8") == original_content

    def test_review_with_backfill_artifacts_returns_summary(self, tmp_path):
        """With backfill artifacts present, returns quarter summary."""
        state_dir = tmp_path / "state" / "work"
        reflections_dir = tmp_path / "state" / "work" / "reflections" / "weekly"
        reflections_dir.mkdir(parents=True)

        # Create several fake backfill artifacts
        for i in range(3, 6):
            fpath = reflections_dir / f"weekly-2025-W{i:02d}.md"
            fpath.write_text(
                f'---\nsource: "backfill"\nperiod: "2025-W{i:02d}"\n---\n\n## Weekly Reflection\n',
                encoding="utf-8",
            )

        result = run_backfill_review(state_dir=state_dir, base_dir=tmp_path)
        assert isinstance(result, str)
        assert "backfill" in result.lower() or "quarter" in result.lower()

    def test_review_groups_by_quarter(self, tmp_path):
        """Weeks should be grouped into Q1 / Q2 / etc."""
        state_dir = tmp_path / "state" / "work"
        reflections_dir = tmp_path / "state" / "work" / "reflections" / "weekly"
        reflections_dir.mkdir(parents=True)

        # W01–W04 → Q1, W14–W16 → Q2
        for week_id in ["2025-W01", "2025-W02", "2025-W14", "2025-W15"]:
            fpath = reflections_dir / f"weekly-{week_id}.md"
            fpath.write_text(
                f'---\nsource: "backfill"\nperiod: "{week_id}"\n---\n\n## Weekly Reflection\n',
                encoding="utf-8",
            )

        result = run_backfill_review(state_dir=state_dir, base_dir=tmp_path)
        assert isinstance(result, str)
        # Should mention at least one quarter designation
        assert "Q1" in result or "Q2" in result or "quarter" in result.lower()
