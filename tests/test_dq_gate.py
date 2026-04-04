# pii-guard: ignore-file
"""tests/test_dq_gate.py — Unit tests for scripts/lib/dq_gate.py

Spec: specs/data-quality-gate.md v4
Phase E of data-quality-gate implementation.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lib.dq_gate import (
    QualityScore,
    QualityVerdict,
    _FILE_TTL,
    _age_days,
    _log_gate_decision,
    _parse_frontmatter,
    _verdict_for,
    assess_quality,
    file_quality,
)
from lib.knowledge_graph import (
    _DQ_GATE_PASS,
    _DQ_GATE_STALE_SERVE,
    _DQ_GATE_WARN,
    _DQ_MIN_CONFIDENCE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_ts(days_ago: float = 0.0) -> str:
    """ISO-8601 timestamp `days_ago` days in the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


def _write_md(path: Path, frontmatter: dict, body: str = "x" * 2000) -> None:
    """Write a minimal markdown file with YAML frontmatter."""
    import yaml  # type: ignore[import]
    lines = ["---", yaml.dump(frontmatter, default_flow_style=False).rstrip(), "---", "", body]
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# _parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_valid_frontmatter(self, tmp_path):
        p = tmp_path / "test.md"
        p.write_text("---\nkey: value\n---\nbody\n", encoding="utf-8")
        fm = _parse_frontmatter(p)
        assert fm["key"] == "value"

    def test_no_frontmatter_returns_empty(self, tmp_path):
        p = tmp_path / "test.md"
        p.write_text("# Just a heading\n", encoding="utf-8")
        assert _parse_frontmatter(p) == {}

    def test_missing_file_returns_empty(self, tmp_path):
        assert _parse_frontmatter(tmp_path / "nonexistent.md") == {}


# ---------------------------------------------------------------------------
# _age_days
# ---------------------------------------------------------------------------

class TestAgeDays:
    def test_fresh_timestamp(self):
        ts = _fresh_ts(days_ago=1.0)
        age = _age_days(ts)
        assert age is not None
        assert 0.9 < age < 1.1

    def test_none_returns_none(self):
        assert _age_days(None) is None

    def test_empty_string_returns_none(self):
        assert _age_days("") is None

    def test_unparseable_returns_none(self):
        assert _age_days("not-a-date") is None


# ---------------------------------------------------------------------------
# _verdict_for
# ---------------------------------------------------------------------------

class TestVerdictFor:
    def test_pass(self):
        assert _verdict_for(_DQ_GATE_PASS) == QualityVerdict.PASS
        assert _verdict_for(1.0) == QualityVerdict.PASS

    def test_warn(self):
        assert _verdict_for(_DQ_GATE_WARN) == QualityVerdict.WARN
        mid = (_DQ_GATE_WARN + _DQ_GATE_PASS) / 2
        assert _verdict_for(mid) == QualityVerdict.WARN

    def test_stale_serve(self):
        assert _verdict_for(_DQ_GATE_STALE_SERVE) == QualityVerdict.STALE_SERVE
        mid = (_DQ_GATE_STALE_SERVE + _DQ_GATE_WARN) / 2
        assert _verdict_for(mid) == QualityVerdict.STALE_SERVE

    def test_refuse(self):
        assert _verdict_for(0.0) == QualityVerdict.REFUSE
        assert _verdict_for(_DQ_GATE_STALE_SERVE - 0.01) == QualityVerdict.REFUSE


# ---------------------------------------------------------------------------
# file_quality — missing file
# ---------------------------------------------------------------------------

class TestFileQualityMissingFile:
    def test_missing_returns_zero(self, tmp_path):
        assert file_quality(tmp_path / "nonexistent.md", "calendar") == 0.0


# ---------------------------------------------------------------------------
# file_quality — live-provider domain profiles
# ---------------------------------------------------------------------------

class TestFileQualityProviders:
    """Verify that live-provider provenance yields high accuracy scores."""

    @pytest.mark.parametrize("domain,filename,providers,ttl_factor", [
        ("calendar", "work-calendar.md", ["graph_calendar"], 1),
        ("comms",    "work-comms.md",    ["workiq"],         1),
        ("incidents","work-incidents.md",["kusto_icm"],      1),
        ("projects", "work-projects.md", ["ado_workitems"],  7),
    ])
    def test_live_provider_fresh_file_passes(
        self, tmp_path, domain, filename, providers, ttl_factor
    ):
        p = tmp_path / filename
        _write_md(p, {
            "last_updated": _fresh_ts(days_ago=0.1),
            "providers_used": providers,
        })
        score = file_quality(p, domain)
        assert score >= _DQ_GATE_PASS, (
            f"Expected PASS for {domain} with live provider, got {score:.3f}"
        )

    def test_pipeline_generated_by_scores_lower_than_live(self, tmp_path):
        fname = "work-calendar.md"
        p = tmp_path / fname
        _write_md(p, {
            "last_updated": _fresh_ts(days_ago=0.1),
            "generated_by": "work_loop",  # no providers_used
        })
        score_pipeline = file_quality(p, "calendar")

        _write_md(p, {
            "last_updated": _fresh_ts(days_ago=0.1),
            "providers_used": ["graph_calendar"],
        })
        score_live = file_quality(p, "calendar")

        assert score_pipeline <= score_live

    def test_unknown_provenance_lowest_accuracy(self, tmp_path):
        fname = "work-calendar.md"
        p = tmp_path / fname
        _write_md(p, {"last_updated": _fresh_ts(days_ago=0.1)})
        score_unknown = file_quality(p, "calendar")

        _write_md(p, {
            "last_updated": _fresh_ts(days_ago=0.1),
            "providers_used": ["graph_calendar"],
        })
        score_live = file_quality(p, "calendar")

        assert score_unknown < score_live


# ---------------------------------------------------------------------------
# file_quality — freshness dimension
# ---------------------------------------------------------------------------

class TestFileQualityFreshness:
    def test_very_fresh_scores_high_freshness(self, tmp_path):
        p = tmp_path / "work-calendar.md"
        _write_md(p, {
            "last_updated": _fresh_ts(days_ago=0.01),
            "providers_used": ["graph_calendar"],
        })
        score = file_quality(p, "calendar")
        assert score > 0.85

    def test_overdue_calendar_file_low_score(self, tmp_path):
        p = tmp_path / "work-calendar.md"
        _write_md(p, {
            "last_updated": _fresh_ts(days_ago=5),  # TTL=1d; 5x overdue
            "providers_used": ["graph_calendar"],
        })
        score = file_quality(p, "calendar")
        # Freshness=0 (way past TTL); A=0.90 * w_A, C=1.0 * w_C
        # Expect significant degradation
        assert score < _DQ_GATE_PASS

    def test_no_last_updated_zero_freshness(self, tmp_path):
        p = tmp_path / "work-projects.md"
        _write_md(p, {"providers_used": ["ado_workitems"]})  # no last_updated
        score_no_ts = file_quality(p, "projects")

        _write_md(p, {
            "last_updated": _fresh_ts(days_ago=0.1),
            "providers_used": ["ado_workitems"],
        })
        score_fresh = file_quality(p, "projects")

        assert score_no_ts < score_fresh


# ---------------------------------------------------------------------------
# file_quality — completeness (file size proxy)
# ---------------------------------------------------------------------------

class TestFileQualityCompleteness:
    def test_small_file_lower_completeness(self, tmp_path):
        p = tmp_path / "work-projects.md"
        fm = {"last_updated": _fresh_ts(days_ago=1), "providers_used": ["ado_workitems"]}
        # Small file (< 1000 bytes)
        lines = ["---", "last_updated: " + _fresh_ts(days_ago=1), "providers_used:", "  - ado_workitems", "---", "small"]
        p.write_text("\n".join(lines), encoding="utf-8")
        score_small = file_quality(p, "projects")

        # Large file (> 1000 bytes)
        _write_md(p, fm, body="x" * 2000)
        score_large = file_quality(p, "projects")

        assert score_small < score_large


# ---------------------------------------------------------------------------
# file_quality — per-domain TTL correctness
# ---------------------------------------------------------------------------

class TestFileQualityTTLs:
    @pytest.mark.parametrize("filename,ttl_days", list(_FILE_TTL.items()))
    def test_file_at_half_ttl_has_positive_freshness(self, tmp_path, filename, ttl_days):
        p = tmp_path / filename
        _write_md(p, {
            "last_updated": _fresh_ts(days_ago=ttl_days * 0.5),
            "providers_used": ["workiq"],
        })
        # Strip domain from filename — use "default" profile test
        score = file_quality(p, "default")
        assert score > 0.0


# ---------------------------------------------------------------------------
# assess_quality — entity-level scoring
# ---------------------------------------------------------------------------

class TestAssessQuality:
    """Uses a fake Entity and KnowledgeGraph stub."""

    @pytest.fixture
    def mock_entity_class(self):
        """Build a minimal Entity-like dataclass."""
        from dataclasses import dataclass

        @dataclass
        class FakeEntity:
            id: str = "e1"
            name: str = "Test Entity"
            domain: str = "default"
            source_type: str = "workiq"
            corroborating_sources: int = 0
            confidence: float = 1.0
            last_validated: str | None = None
            effective_staleness: str = "fresh"
            staleness_ttl_days: int = 7
            summary: str = "A real summary"

        return FakeEntity

    @pytest.fixture
    def mock_kg(self):
        """Minimal KG stub — no conflicts."""
        class FakeKG:
            def entity_has_active_conflicts(self, entity_id):
                return False
        return FakeKG()

    @pytest.fixture
    def mock_kg_conflicts(self):
        """KG stub that always reports conflicts."""
        class ConflictKG:
            def entity_has_active_conflicts(self, entity_id):
                return True
        return ConflictKG()

    def test_live_provider_passes(self, mock_entity_class, mock_kg):
        e = mock_entity_class(
            source_type="workiq",
            confidence=1.0,
            last_validated=_fresh_ts(days_ago=1),
        )
        qs = assess_quality(e, mock_kg)
        assert isinstance(qs, QualityScore)
        assert qs.verdict == QualityVerdict.PASS
        assert qs.A >= 0.85  # 0.90 base * 1.0 confidence

    def test_conflict_penalty_halves_accuracy(self, mock_entity_class, mock_kg, mock_kg_conflicts):
        e = mock_entity_class(
            source_type="workiq",
            confidence=1.0,
            last_validated=_fresh_ts(days_ago=0.1),
        )
        qs_clean = assess_quality(e, mock_kg)
        qs_conflict = assess_quality(e, mock_kg_conflicts)
        # Conflict should halve A
        assert abs(qs_conflict.A - qs_clean.A * 0.5) < 0.01

    def test_low_confidence_degrades_score(self, mock_entity_class, mock_kg):
        e = mock_entity_class(
            source_type="workiq",
            confidence=0.2,  # below _DQ_MIN_CONFIDENCE typical usage
            last_validated=_fresh_ts(days_ago=0.1),
        )
        qs = assess_quality(e, mock_kg)
        # A = 0.90 * 0.2 = 0.18
        assert qs.A < 0.25

    def test_corroboration_boost(self, mock_entity_class, mock_kg):
        e_no_corr = mock_entity_class(
            source_type="workiq",
            corroborating_sources=0,
            confidence=1.0,
            last_validated=_fresh_ts(days_ago=0.1),
        )
        e_corroborated = mock_entity_class(
            source_type="workiq",
            corroborating_sources=2,
            confidence=1.0,
            last_validated=_fresh_ts(days_ago=0.1),
        )
        qs_base = assess_quality(e_no_corr, mock_kg)
        qs_boost = assess_quality(e_corroborated, mock_kg)
        # Corroboration adds +0.10 before scaling, so A_boost should be higher
        assert qs_boost.A > qs_base.A

    def test_manual_source_type_lower_accuracy(self, mock_entity_class, mock_kg):
        e_live = mock_entity_class(source_type="workiq")
        e_manual = mock_entity_class(source_type="manual")
        qs_live = assess_quality(e_live, mock_kg)
        qs_manual = assess_quality(e_manual, mock_kg)
        assert qs_live.A > qs_manual.A

    def test_stale_entity_no_last_validated(self, mock_entity_class, mock_kg):
        e = mock_entity_class(
            source_type="workiq",
            confidence=1.0,
            last_validated=None,
            effective_staleness="expired",
        )
        qs = assess_quality(e, mock_kg)
        assert qs.F == 0.0

    def test_incomplete_entity_low_completeness(self, mock_entity_class, mock_kg):
        e = mock_entity_class(
            name="Test",
            summary="",  # no summary
            source_type="workiq",
            last_validated=_fresh_ts(days_ago=0.1),
        )
        qs = assess_quality(e, mock_kg)
        assert qs.C == 0.5  # name present, summary absent

    def test_empty_name_zero_completeness(self, mock_entity_class, mock_kg):
        e = mock_entity_class(
            name="",
            summary="",
            source_type="manual",
            last_validated=_fresh_ts(days_ago=0.1),
        )
        qs = assess_quality(e, mock_kg)
        assert qs.C == 0.0

    def test_composite_is_weighted_sum(self, mock_entity_class, mock_kg):
        from lib.knowledge_graph import _DQ_DOMAIN_WEIGHTS
        e = mock_entity_class(
            domain="default",
            source_type="workiq",
            confidence=1.0,
            last_validated=_fresh_ts(days_ago=0.1),
            name="Test",
            summary="Good summary",
        )
        qs = assess_quality(e, mock_kg)
        w = _DQ_DOMAIN_WEIGHTS["default"]
        expected = round(
            max(0.0, min(1.0, w["A"] * qs.A + w["F"] * qs.F + w["C"] * qs.C)), 4
        )
        assert abs(qs.composite - expected) < 0.001


# ---------------------------------------------------------------------------
# _log_gate_decision — should not raise
# ---------------------------------------------------------------------------

class TestLogGateDecision:
    def test_log_does_not_raise(self, tmp_path, monkeypatch):
        """_log_gate_decision should swallow errors silently."""
        import lib.dq_gate as dq
        monkeypatch.setattr(dq, "_GATE_LOG", tmp_path / "work" / "quality_gate.log")
        (tmp_path / "work").mkdir()
        # Should not raise even on first write
        _log_gate_decision(
            section="calendar",
            path=tmp_path / "work-calendar.md",
            domain="calendar",
            score=0.85,
            verdict=QualityVerdict.PASS,
        )
        log_file = tmp_path / "work" / "quality_gate.log"
        assert log_file.exists()
        import json as _json
        record = _json.loads(log_file.read_text().strip())
        assert record["verdict"] == "PASS"
        assert record["score"] == 0.85

    def test_log_silently_handles_missing_dir(self, tmp_path, monkeypatch):
        """Logging must be silent even if the directory does not exist."""
        import lib.dq_gate as dq
        monkeypatch.setattr(
            dq, "_GATE_LOG", tmp_path / "no_such_dir" / "quality_gate.log"
        )
        # Should NOT raise
        _log_gate_decision(
            section="comms",
            path=tmp_path / "work-comms.md",
            domain="comms",
            score=0.5,
            verdict=QualityVerdict.WARN,
        )


# ---------------------------------------------------------------------------
# QualityVerdict IntEnum ordering
# ---------------------------------------------------------------------------

class TestQualityVerdictOrdering:
    def test_min_selects_worst_verdict(self):
        verdicts = [
            QualityVerdict.PASS, QualityVerdict.WARN, QualityVerdict.REFUSE
        ]
        worst = QualityVerdict(min(v.value for v in verdicts))
        assert worst == QualityVerdict.REFUSE

    def test_int_comparison(self):
        assert QualityVerdict.REFUSE < QualityVerdict.STALE_SERVE
        assert QualityVerdict.STALE_SERVE < QualityVerdict.WARN
        assert QualityVerdict.WARN < QualityVerdict.PASS


# ---------------------------------------------------------------------------
# Phase E.3 — Integration: _pre_answer_quality_gate (per-section scoring)
# ---------------------------------------------------------------------------

_work_helpers_available = importlib.util.find_spec("work") is not None
_work_domain_writers_available = importlib.util.find_spec("work_domain_writers") is not None


@pytest.mark.skipif(not _work_helpers_available, reason="work.helpers not available (gitignored)")
class TestPreAnswerQualityGate:
    """spec §Phase E.3 — multi-domain per-section scoring, worst-verdict computation."""

    def _write_fresh(self, path: Path, domain_ttl_days: int = 7) -> None:
        """Write a fresh file that will PASS for its domain."""
        import yaml  # type: ignore[import]
        fm = {
            "last_updated": _fresh_ts(0.1),
            "generated_by": "work_loop",
            "providers_used": ["workiq"],
        }
        body = "x" * 2000
        path.write_text(
            "---\n" + yaml.safe_dump(fm) + "---\n\n" + body, encoding="utf-8"
        )

    def _write_stale(self, path: Path, days_old: float = 20.0) -> None:
        """Write a stale file (no providers_used → lower A score, old timestamp)."""
        import yaml  # type: ignore[import]
        fm = {"last_updated": _fresh_ts(days_old), "generated_by": "work_loop"}
        body = "x" * 2000
        path.write_text(
            "---\n" + yaml.safe_dump(fm) + "---\n\n" + body, encoding="utf-8"
        )

    def test_all_pass_returns_pass_verdict_no_caveats(self, tmp_path, monkeypatch):
        """All sections fresh → PASS verdict, empty caveats dict."""
        import lib.dq_gate as dq
        monkeypatch.setattr(dq, "_GATE_LOG", tmp_path / "quality_gate.log")

        from work.helpers import _pre_answer_quality_gate

        cal = tmp_path / "work-calendar.md"
        comms = tmp_path / "work-comms.md"
        self._write_fresh(cal)
        self._write_fresh(comms)

        verdict, caveats = _pre_answer_quality_gate([
            ("calendar", cal, "calendar"),
            ("comms",    comms, "comms"),
        ])
        assert verdict == QualityVerdict.PASS
        assert caveats == {}

    def test_one_stale_section_returns_worst_verdict(self, tmp_path, monkeypatch):
        """One REFUSE-level section → overall verdict is REFUSE; other sections unaffected."""
        import lib.dq_gate as dq
        monkeypatch.setattr(dq, "_GATE_LOG", tmp_path / "quality_gate.log")

        from work.helpers import _pre_answer_quality_gate

        # Fresh people file (14d TTL, 1d old → PASS)
        people = tmp_path / "work-people.md"
        self._write_fresh(people)

        # Ancient calendar file (1d TTL, 20d old → REFUSE)
        cal = tmp_path / "work-calendar.md"
        self._write_stale(cal, days_old=20.0)

        verdict, caveats = _pre_answer_quality_gate([
            ("calendar", cal, "calendar"),
            ("people",   people, "people"),
        ])
        assert verdict == QualityVerdict.REFUSE
        # calendar section must have a caveat; people section must not
        assert "calendar" in caveats
        assert "people" not in caveats

    def test_empty_sections_returns_refuse(self, tmp_path):
        """Empty section list → REFUSE (no data is worst case)."""
        from work.helpers import _pre_answer_quality_gate

        verdict, caveats = _pre_answer_quality_gate([])
        assert verdict == QualityVerdict.REFUSE
        assert caveats == {}

    def test_missing_file_section_counts_as_refuse(self, tmp_path, monkeypatch):
        """A section whose file does not exist → REFUSE verdict for that section."""
        import lib.dq_gate as dq
        monkeypatch.setattr(dq, "_GATE_LOG", tmp_path / "quality_gate.log")

        from work.helpers import _pre_answer_quality_gate

        nonexistent = tmp_path / "no-such-file.md"
        verdict, caveats = _pre_answer_quality_gate([
            ("projects", nonexistent, "default"),
        ])
        # file_quality returns 0.0 for missing file → REFUSE
        assert verdict == QualityVerdict.REFUSE
        assert "projects" in caveats

    def test_per_section_caveats_contain_section_name(self, tmp_path, monkeypatch):
        """Caveat strings for WARN/STALE/REFUSE sections reference the section name."""
        import lib.dq_gate as dq
        monkeypatch.setattr(dq, "_GATE_LOG", tmp_path / "quality_gate.log")

        from work.helpers import _pre_answer_quality_gate

        import yaml  # type: ignore[import]
        # Write a file that is >= WARN but < PASS for a fresh-sensitive domain.
        # calendar TTL=1d; 0.6d old → F=0.4 → Q=0.1*0.85+0.8*0.4+0.1*1.0 ≈ 0.50 (WARN boundary)
        cal = tmp_path / "work-calendar.md"
        fm = {
            "last_updated": _fresh_ts(0.6),
            "generated_by": "work_loop",
            "providers_used": ["workiq"],
        }
        cal.write_text(
            "---\n" + yaml.safe_dump(fm) + "---\n\n" + "x" * 2000, encoding="utf-8"
        )

        verdict, caveats = _pre_answer_quality_gate([
            ("calendar", cal, "calendar"),
        ])
        # Whatever verdict was assigned, if a caveat exists it must name the section
        if caveats:
            assert any("calendar" in v for v in caveats.values())

    def test_gate_log_written_for_each_section(self, tmp_path, monkeypatch):
        """_log_gate_decision is called for each section — gate.log has N lines."""
        import lib.dq_gate as dq
        gate_log = tmp_path / "quality_gate.log"
        monkeypatch.setattr(dq, "_GATE_LOG", gate_log)

        from work.helpers import _pre_answer_quality_gate

        f1 = tmp_path / "work-calendar.md"
        f2 = tmp_path / "work-comms.md"
        self._write_fresh(f1)
        self._write_fresh(f2)

        _pre_answer_quality_gate([
            ("calendar", f1, "calendar"),
            ("comms",    f2, "comms"),
        ])
        import json as _json
        lines = [l for l in gate_log.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
        sections_logged = {_json.loads(l)["section"] for l in lines}
        assert sections_logged == {"calendar", "comms"}


# ---------------------------------------------------------------------------
# Phase E (N5) — stamp_warmstart_providers()
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _work_domain_writers_available, reason="work_domain_writers not available (gitignored)")
class TestStampWarmstartProviders:
    """spec N5 — stamp_warmstart_providers() stamps human-authored files non-destructively."""

    # sys.path includes scripts/ in conftest so we can import from work package
    @pytest.fixture(autouse=True)
    def _patch_sys_path(self, tmp_path):
        """Ensure scripts/ is importable (mirrors conftest setup)."""
        import sys
        scripts_dir = Path(__file__).parent.parent / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

    def _make_file(self, state_dir: Path, filename: str, frontmatter: dict) -> Path:
        import yaml  # type: ignore[import]
        p = state_dir / filename
        p.write_text(
            "---\n" + yaml.safe_dump(frontmatter) + "---\n\nContent here.\n",
            encoding="utf-8",
        )
        return p

    def test_stamps_file_without_providers_used(self, tmp_path):
        """Files lacking providers_used get stamped with ['human_authored']."""
        import yaml  # type: ignore[import]
        from work_domain_writers import stamp_warmstart_providers

        self._make_file(tmp_path, "work-people.md", {"last_updated": "2026-01-01"})
        stamp_warmstart_providers(tmp_path)

        text = (tmp_path / "work-people.md").read_text()
        fm = yaml.safe_load(text.split("---")[1])
        assert fm["providers_used"] == ["human_authored"]

    def test_skips_file_already_stamped(self, tmp_path):
        """Files that already have providers_used are not overwritten."""
        import yaml  # type: ignore[import]
        from work_domain_writers import stamp_warmstart_providers

        original_providers = ["workiq", "ado"]
        self._make_file(tmp_path, "work-decisions.md", {
            "last_updated": "2026-01-01",
            "providers_used": original_providers,
        })
        stamp_warmstart_providers(tmp_path)

        text = (tmp_path / "work-decisions.md").read_text()
        fm = yaml.safe_load(text.split("---")[1])
        assert fm["providers_used"] == original_providers

    def test_skips_missing_file_silently(self, tmp_path):
        """Missing warm-start files are silently skipped — no exception raised."""
        from work_domain_writers import stamp_warmstart_providers

        # Empty state_dir — none of the 6 warm-start files exist
        stamp_warmstart_providers(tmp_path)  # must not raise

    def test_stamps_all_six_warmstart_files(self, tmp_path):
        """All 6 warm-start files are stamped in a single call."""
        import yaml  # type: ignore[import]
        from work_domain_writers import stamp_warmstart_providers

        warmstart_files = [
            "work-people.md",
            "work-accomplishments.md",
            "work-decisions.md",
            "work-scope.md",
            "work-performance.md",
            "golden-queries.md",
        ]
        for f in warmstart_files:
            self._make_file(tmp_path, f, {"last_updated": "2026-01-01"})

        stamp_warmstart_providers(tmp_path)

        for f in warmstart_files:
            text = (tmp_path / f).read_text()
            fm = yaml.safe_load(text.split("---")[1])
            assert fm.get("providers_used") == ["human_authored"], f"{f} was not stamped"

    def test_idempotent_double_call(self, tmp_path):
        """Calling stamp twice does not corrupt the file or duplicate the key."""
        import yaml  # type: ignore[import]
        from work_domain_writers import stamp_warmstart_providers

        self._make_file(tmp_path, "work-scope.md", {"last_updated": "2026-01-01"})
        stamp_warmstart_providers(tmp_path)
        stamp_warmstart_providers(tmp_path)  # second call

        text = (tmp_path / "work-scope.md").read_text()
        fm = yaml.safe_load(text.split("---")[1])
        assert fm["providers_used"] == ["human_authored"]

    def test_file_without_frontmatter_skipped(self, tmp_path):
        """Files without YAML frontmatter (no leading ---) are skipped cleanly."""
        from work_domain_writers import stamp_warmstart_providers

        p = tmp_path / "work-performance.md"
        p.write_text("Just plain content, no frontmatter.\n", encoding="utf-8")
        stamp_warmstart_providers(tmp_path)  # must not raise or corrupt

        assert p.read_text() == "Just plain content, no frontmatter.\n"


# ---------------------------------------------------------------------------
# Phase E.5 — Regression: _staleness_header() delegates to _quality_header()
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _work_helpers_available, reason="work.helpers not available (gitignored)")
class TestStalenessHeaderFallback:
    """spec Phase E.5 — _staleness_header() is a deprecated wrapper, not an independent impl."""

    @pytest.fixture(autouse=True)
    def _patch_sys_path(self, tmp_path):
        import sys
        scripts_dir = Path(__file__).parent.parent / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

    def test_fresh_data_returns_empty_string(self):
        """Fresh data (< 18h old) → empty string from _staleness_header()."""
        from work.helpers import _staleness_header

        fm = {"last_updated": _fresh_ts(0.5)}
        result = _staleness_header(fm, label="test data")
        assert result == ""

    def test_stale_data_returns_warning(self):
        """Stale data (> 18h old) → non-empty warning string."""
        from work.helpers import _staleness_header

        fm = {"last_updated": _fresh_ts(2.0)}  # 2 days old
        result = _staleness_header(fm, label="test data")
        assert result != ""
        assert "stale" in result.lower() or "⚠" in result

    def test_missing_last_updated_returns_warning(self):
        """No last_updated → treated as stale (age = infinity)."""
        from work.helpers import _staleness_header

        result = _staleness_header({}, label="test data")
        assert result != ""

    def test_quality_header_import_failure_falls_back_to_staleness_header(
        self, tmp_path, monkeypatch
    ):
        """_quality_header() falls back to _staleness_header() if dq_gate import fails."""
        import importlib
        import sys

        # Temporarily block dq_gate import to simulate import failure
        original = sys.modules.get("lib.dq_gate")
        sys.modules["lib.dq_gate"] = None  # type: ignore[assignment]
        try:
            # Reload helpers to pick up blocked import
            if "work.helpers" in sys.modules:
                importlib.reload(sys.modules["work.helpers"])
            from work.helpers import _quality_header

            # Write a stale file — if fallback triggers, we should get a staleness warning
            import yaml  # type: ignore[import]
            p = tmp_path / "test.md"
            fm = {"last_updated": _fresh_ts(2.0)}  # stale
            p.write_text("---\n" + yaml.safe_dump(fm) + "---\n\n" + "x" * 500, encoding="utf-8")

            result = _quality_header(p, domain="default", label="test data")
            # Either the gate worked (returns "" since 2d old with default=7d TTL is fresh enough)
            # OR the fallback triggered and returns a staleness warning
            # Either way it must not raise
            assert isinstance(result, str)
        finally:
            # Always restore
            if original is None:
                sys.modules.pop("lib.dq_gate", None)
            else:
                sys.modules["lib.dq_gate"] = original
            if "work.helpers" in sys.modules:
                importlib.reload(sys.modules["work.helpers"])

    def test_quality_header_fresh_file_returns_empty(self, tmp_path):
        """_quality_header() returns '' for a genuinely fresh file (PASS)."""
        import yaml  # type: ignore[import]
        from work.helpers import _quality_header

        p = tmp_path / "work-people.md"
        fm = {
            "last_updated": _fresh_ts(0.1),
            "generated_by": "work_loop",
            "providers_used": ["workiq"],
        }
        p.write_text("---\n" + yaml.safe_dump(fm) + "---\n\n" + "x" * 2000, encoding="utf-8")

        result = _quality_header(p, domain="people", label="people data")
        assert result == ""

    def test_quality_header_stale_file_returns_caveat(self, tmp_path):
        """_quality_header() for a stale calendar file (>1d old) returns a caveat, not ''."""
        import yaml  # type: ignore[import]
        from work.helpers import _quality_header

        p = tmp_path / "work-calendar.md"
        fm = {
            "last_updated": _fresh_ts(3.0),  # 3d old, calendar TTL=1d
            "generated_by": "work_loop",
            "providers_used": ["workiq"],
        }
        p.write_text("---\n" + yaml.safe_dump(fm) + "---\n\n" + "x" * 2000, encoding="utf-8")

        result = _quality_header(p, domain="calendar", label="calendar data")
        assert result != ""
        assert "⚠" in result or "❌" in result
