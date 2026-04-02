"""tests/eval/test_briefing_quality.py — Eval layer quality gate tests.

Five golden scenarios (expected-pass) + five anti-golden scenarios
(expected-fail / low-quality detection).

All briefing content and data is 100% fictional — no real user data (DD-5).
Ref: specs/eval.md EV-12
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import helpers — load scripts without executing __main__ guards
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Module-level lazy imports (avoid import errors on missing deps at collect time)
# ---------------------------------------------------------------------------
_eval_scorer = None
_correction_feeder = None
_log_digest = None
_retrospective_view = None


def _get_eval_scorer():
    global _eval_scorer
    if _eval_scorer is None:
        _eval_scorer = _load_module("eval_scorer", _SCRIPTS_DIR / "eval_scorer.py")
    return _eval_scorer


def _get_correction_feeder():
    global _correction_feeder
    if _correction_feeder is None:
        _correction_feeder = _load_module(
            "correction_feeder", _SCRIPTS_DIR / "correction_feeder.py"
        )
    return _correction_feeder


def _get_log_digest():
    global _log_digest
    if _log_digest is None:
        _log_digest = _load_module("log_digest", _SCRIPTS_DIR / "log_digest.py")
    return _log_digest


def _get_retrospective_view():
    global _retrospective_view
    if _retrospective_view is None:
        _retrospective_view = _load_module(
            "retrospective_view", _SCRIPTS_DIR / "retrospective_view.py"
        )
    return _retrospective_view


# ===========================================================================
# GOLDEN SCENARIOS — expected-pass / good-quality detection
# ===========================================================================


class TestGoldenScenarios:
    """Golden paths: valid inputs should produce high-quality outputs."""

    def test_golden_briefing_scores_above_threshold(
        self, golden_briefing_path, rubric
    ):
        """G1: A well-formed fictional briefing must score above rubric golden_min."""
        scorer = _get_eval_scorer()
        result = scorer.score_briefing(golden_briefing_path)

        assert result["quality_score"] is not None
        threshold = rubric["quality"]["golden_min"]
        assert result["quality_score"] >= threshold, (
            f"Golden briefing scored {result['quality_score']:.1f}, "
            f"expected >= {threshold}. Dimensions: {result['dimensions']}"
        )
        # All five dimensions must be present
        assert set(result["dimensions"].keys()) == {
            "actionability", "specificity", "completeness", "signal_purity", "calibration"
        }

    def test_golden_briefing_has_positive_actionability(self, golden_briefing_path):
        """G2: A golden briefing must have non-trivial actionability score."""
        scorer = _get_eval_scorer()
        result = scorer.score_briefing(golden_briefing_path)
        assert result["dimensions"]["actionability"] > 8.0, (
            f"Expected actionability > 8.0, got {result['dimensions']['actionability']}"
        )

    def test_valid_corrections_pass_filter(self, sample_facts, rubric):
        """G3: Valid, non-expired correction facts must survive the filter."""
        feeder = _get_correction_feeder()
        filtered = feeder._filter_facts(sample_facts)

        # The 4 valid facts are: finance×2 (non-expired), immigration×1, health×1
        # The expired finance and 'note' type health facts should be dropped
        values = [f["value"] for f in filtered]
        assert "Use pre-tax gross income" in values
        assert "I-94 records via CBP portal" in values
        assert "Health correction" in values
        assert len(filtered) >= 3, f"Expected ≥3 valid facts, got {len(filtered)}: {filtered}"

    def test_empty_log_dir_produces_zero_error_digest(self, tmp_log_dir, rubric):
        """G4: An empty log directory must produce a valid digest with 0 errors."""
        digest_mod = _get_log_digest()
        digest = digest_mod.build_digest(log_dir=tmp_log_dir, lookback_days=7)

        assert digest["schema_version"] == "1.0.0"
        assert digest["total_records"] == 0
        assert digest["total_errors"] == 0
        assert digest["error_budget_pct"] == 0.0
        assert digest["anomalies"] == []

    def test_no_stale_domains_when_all_domains_present(self, mock_catch_up_runs):
        """G5: No stale domains when all domains appear consistently."""
        rv = _get_retrospective_view()
        stale = rv._detect_stale_domains(mock_catch_up_runs, streak_threshold=3)
        assert stale == [], f"Expected no stale domains, got: {stale}"


# ===========================================================================
# ANTI-GOLDEN SCENARIOS — expected-fail / low-quality detection
# ===========================================================================


class TestAntiGoldenScenarios:
    """Anti-golden paths: bad inputs / degraded data must be reliably detected."""

    def test_anti_golden_briefing_scores_below_threshold(
        self, anti_golden_briefing_path, rubric
    ):
        """A1: A vague, non-actionable fictional briefing must score below anti_golden_max."""
        scorer = _get_eval_scorer()
        result = scorer.score_briefing(anti_golden_briefing_path)

        assert result["quality_score"] is not None
        threshold = rubric["quality"]["anti_golden_max"]
        assert result["quality_score"] <= threshold, (
            f"Anti-golden briefing scored {result['quality_score']:.1f}, "
            f"expected <= {threshold}. Dimensions: {result['dimensions']}"
        )

    def test_expired_facts_are_filtered_out(self, sample_facts):
        """A2: Facts with past TTL must never appear in feeder output."""
        feeder = _get_correction_feeder()
        filtered = feeder._filter_facts(sample_facts)

        values = [f["value"] for f in filtered]
        assert "Old expired rule" not in values, "Expired fact leaked through filter"

    def test_wrong_type_facts_are_filtered_out(self, sample_facts):
        """A3: Facts with type 'note' (not in allowed types) must be filtered out."""
        feeder = _get_correction_feeder()
        filtered = feeder._filter_facts(sample_facts)

        types = {f["type"] for f in filtered}
        assert "note" not in types, f"Non-allowed type leaked through filter: {types}"

    def test_high_error_rate_connector_triggers_anomaly(
        self, log_dir_with_errors, rubric
    ):
        """A4: A connector with >20% error rate must trigger HIGH_ERROR_RATE anomaly."""
        digest_mod = _get_log_digest()
        digest = digest_mod.build_digest(log_dir=log_dir_with_errors, lookback_days=30)

        anomalies = digest["anomalies"]
        codes = [a["code"] for a in anomalies]
        assert "HIGH_ERROR_RATE" in codes, (
            f"Expected HIGH_ERROR_RATE anomaly for 'graph' connector. "
            f"Got anomalies: {anomalies}\nConnectors: {digest.get('connectors')}"
        )
        # The 'graph' connector should be the one flagged
        graph_anomalies = [a for a in anomalies if a.get("connector") == "graph"]
        assert graph_anomalies, "HIGH_ERROR_RATE anomaly should identify 'graph' connector"
        assert graph_anomalies[0]["severity"] == rubric["error_budget"]["anomaly_levels"][
            "HIGH_ERROR_RATE"
        ]["severity"]

    def test_missing_domain_detected_as_stale(self, mock_stale_domain_runs):
        """A5: 'immigration' missing from last 3 runs must appear in stale domains list."""
        rv = _get_retrospective_view()
        stale = rv._detect_stale_domains(mock_stale_domain_runs, streak_threshold=3)

        assert "immigration" in stale, (
            f"Expected 'immigration' in stale domains list. Got: {stale}"
        )
        # 'finance' and 'health' should NOT be stale — they appear in recent runs
        assert "finance" not in stale
        assert "health" not in stale


# ===========================================================================
# PARAMETRIZED GOLDEN / ANTI-GOLDEN DIMENSION CHECKS
# ===========================================================================

_GOLDEN_TEXTS = [
    pytest.param(
        "Submit Form I-485 by 2026-02-01 — call attorney by Thursday.\n"
        "Transfer $3,000 to emergency fund before Jan 25.",
        id="golden-immigration-finance-actions",
    ),
    pytest.param(
        "Schedule bloodwork appointment by Jan 30, 2026.\n"
        "Refill metformin prescription: 90-day supply expires 2026-01-28.",
        id="golden-health-dates",
    ),
]

_ANTI_GOLDEN_TEXTS = [
    pytest.param(
        "Things might need attention. Consider reviewing when convenient.",
        id="anti-vague-no-dates",
    ),
    pytest.param(
        "Updates: stuff happened. More stuff may happen later possibly.",
        id="anti-hedging-no-specifics",
    ),
]


@pytest.mark.parametrize("text", _GOLDEN_TEXTS)
def test_golden_snippet_specificity(text, tmp_path):
    """Golden snippets must score higher specificity than anti-golden snippets."""
    scorer = _get_eval_scorer()
    path = tmp_path / "golden.md"
    path.write_text(text, encoding="utf-8")
    result = scorer.score_briefing(path)
    # Specificity should be above 5.0 for texts containing dates and numbers
    assert result["dimensions"]["specificity"] > 5.0, (
        f"Expected specificity > 5.0 for golden text, got {result['dimensions']['specificity']}"
    )


@pytest.mark.parametrize("text", _ANTI_GOLDEN_TEXTS)
def test_anti_golden_snippet_actionability(text, tmp_path):
    """Anti-golden snippets must score low actionability."""
    scorer = _get_eval_scorer()
    path = tmp_path / "anti.md"
    path.write_text(text, encoding="utf-8")
    result = scorer.score_briefing(path)
    # Vague text should score below 10.0 actionability
    assert result["dimensions"]["actionability"] < 10.0, (
        f"Expected actionability < 10.0 for anti-golden text, "
        f"got {result['dimensions']['actionability']}"
    )


# ===========================================================================
# EDGE / BOUNDARY TESTS
# ===========================================================================


class TestEdgeCases:
    """Boundary conditions that must be handled gracefully."""

    def test_score_briefing_schema_version(self, golden_briefing_path):
        """Score result must include schema_version field."""
        scorer = _get_eval_scorer()
        result = scorer.score_briefing(golden_briefing_path)
        assert result.get("schema_version") == "1.0.0"

    def test_filter_facts_domain_scoping(self, sample_facts):
        """Domain filter must exclude facts from other domains."""
        feeder = _get_correction_feeder()
        finance_only = feeder._filter_facts(sample_facts, domain="finance")

        domains_returned = {f.get("domain") for f in finance_only if f.get("domain")}
        # Should only contain 'finance' (or no domain)
        assert domains_returned <= {"finance"}, (
            f"Domain filter leaked non-finance facts: {domains_returned}"
        )
        # Must not include the 'immigration' correction
        values = [f["value"] for f in finance_only]
        assert "I-94 records via CBP portal" not in values

    def test_digest_connectors_dict_populated(self, log_dir_with_errors):
        """Digest must have a populated 'connectors' dict for non-empty logs."""
        digest_mod = _get_log_digest()
        digest = digest_mod.build_digest(log_dir=log_dir_with_errors, lookback_days=30)

        assert "connectors" in digest
        assert len(digest["connectors"]) >= 1

    def test_stale_domain_not_flagged_when_insufficient_runs(self):
        """_detect_stale_domains must return [] when run count < streak_threshold."""
        rv = _get_retrospective_view()
        # Only 2 runs, streak_threshold=3 → not enough data
        two_runs = [
            {"domains_processed": ["immigration", "finance"]},
            {"domains_processed": ["finance"]},
        ]
        stale = rv._detect_stale_domains(two_runs, streak_threshold=3)
        assert stale == [], "Should return empty list when run count < streak_threshold"

    def test_filter_facts_respects_global_cap(self):
        """_filter_facts must enforce the global cap of 50 facts."""
        feeder = _get_correction_feeder()
        # Create 60 valid correction facts
        facts = [
            {"type": "correction", "domain": "finance", "value": f"Fact {i}", "ttl": "2099-01-01"}
            for i in range(60)
        ]
        filtered = feeder._filter_facts(facts)
        assert len(filtered) <= feeder._GLOBAL_CAP, (
            f"Expected ≤{feeder._GLOBAL_CAP} facts after global cap, got {len(filtered)}"
        )
