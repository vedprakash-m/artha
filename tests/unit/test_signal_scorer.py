"""tests/unit/test_signal_scorer.py — Unit tests for scripts/lib/signal_scorer.py

Wave 1 verification suite (specs/agent-fw.md §AFW-9).

Coverage:
  - score_signal: min/max/mid urgency, impact, freshness decay, boundary conditions
  - rank_signals: descending sort, _score key injection, original dicts not mutated
  - partition_signals: promoted/normal/suppressed buckets, empty input, edge scores
  - Weight overrides and config defaults
"""
from __future__ import annotations

import pytest

# conftest adds scripts/ to sys.path
from lib.signal_scorer import (
    _DEFAULT_PROMOTE_ABOVE,
    _DEFAULT_SUPPRESS_BELOW,
    partition_signals,
    rank_signals,
    score_signal,
)


# ---------------------------------------------------------------------------
# score_signal
# ---------------------------------------------------------------------------

class TestScoreSignal:
    def test_max_score_no_age(self):
        """urgency=5, impact=5, age_days=0 → maximum composite score."""
        s = score_signal({"urgency": 5, "impact": 5, "age_days": 0})
        assert s == pytest.approx(0.4 * 1.0 + 0.4 * 1.0 + 0.2 * 1.0)  # 1.0

    def test_min_score_no_age(self):
        """urgency=1, impact=1, age_days=0 → minimum non-zero score."""
        s = score_signal({"urgency": 1, "impact": 1, "age_days": 0})
        assert s == pytest.approx(0.4 * 0.2 + 0.4 * 0.2 + 0.2 * 1.0)  # 0.36

    def test_defaults_on_missing_keys(self):
        """Missing urgency, impact, age_days default to 1, 1, 0."""
        s_explicit = score_signal({"urgency": 1, "impact": 1, "age_days": 0})
        s_implicit = score_signal({})
        assert s_explicit == pytest.approx(s_implicit)

    def test_freshness_decay_at_10_days(self):
        """default decay=0.1/day → freshness=0 at age_days=10."""
        # freshness = max(0, 1.0 - 0.1 * 10) = 0.0
        s = score_signal({"urgency": 5, "impact": 5, "age_days": 10})
        assert s == pytest.approx(0.4 * 1.0 + 0.4 * 1.0 + 0.2 * 0.0)  # 0.80

    def test_freshness_clamps_at_zero(self):
        """age_days > 10 → freshness stays 0, score same as age_days=10."""
        s10 = score_signal({"urgency": 5, "impact": 5, "age_days": 10})
        s99 = score_signal({"urgency": 5, "impact": 5, "age_days": 99})
        assert s10 == pytest.approx(s99)

    def test_partial_freshness(self):
        """age_days=5 with default decay=0.1 → freshness=0.5."""
        # score = 0.4*(5/5) + 0.4*(3/5) + 0.2*0.5
        s = score_signal({"urgency": 5, "impact": 3, "age_days": 5})
        assert s == pytest.approx(0.4 * 1.0 + 0.4 * 0.6 + 0.2 * 0.5)

    def test_custom_weights(self):
        """Explicit weight overrides are applied."""
        w = {"urgency_weight": 1.0, "impact_weight": 0.0, "freshness_weight": 0.0,
             "freshness_decay_per_day": 0.1}
        s = score_signal({"urgency": 5, "impact": 1, "age_days": 0}, weights=w)
        assert s == pytest.approx(1.0)

    def test_result_in_range(self):
        """Score should always be in [0.0, 1.0] for valid input."""
        for urgency in range(1, 6):
            for impact in range(1, 6):
                for age in [0, 5, 10, 20]:
                    s = score_signal({"urgency": urgency, "impact": impact, "age_days": age})
                    assert 0.0 <= s <= 1.0 + 1e-9, f"Out of range: {s}"


# ---------------------------------------------------------------------------
# rank_signals
# ---------------------------------------------------------------------------

class TestRankSignals:
    def test_sorted_descending(self):
        """Signals are sorted by _score, highest first."""
        signals = [
            {"urgency": 1, "impact": 1, "age_days": 5},   # low
            {"urgency": 5, "impact": 5, "age_days": 0},   # high
            {"urgency": 3, "impact": 3, "age_days": 2},   # mid
        ]
        ranked = rank_signals(signals)
        scores = [r["_score"] for r in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_score_key_injected(self):
        """Output dicts contain the _score key."""
        ranked = rank_signals([{"urgency": 3, "impact": 3, "age_days": 0}])
        assert "_score" in ranked[0]
        assert isinstance(ranked[0]["_score"], float)

    def test_original_not_mutated(self):
        """Input signal dicts are not modified."""
        sig = {"urgency": 3, "impact": 3, "age_days": 0}
        rank_signals([sig])
        assert "_score" not in sig

    def test_empty_list(self):
        """Empty input returns empty list."""
        assert rank_signals([]) == []

    def test_single_signal(self):
        """Single signal is returned wrapped in a list with _score."""
        result = rank_signals([{"urgency": 4, "impact": 4}])
        assert len(result) == 1
        assert "_score" in result[0]

    def test_weight_override(self):
        """Explicit weight override changes scores."""
        signals = [
            {"urgency": 5, "impact": 1},  # high urgency, low impact
            {"urgency": 1, "impact": 5},  # low urgency, high impact
        ]
        # Urgency-only weights → high-urgency first
        w_urgency = {"urgency_weight": 1.0, "impact_weight": 0.0, "freshness_weight": 0.0,
                     "freshness_decay_per_day": 0.1}
        ranked = rank_signals(signals, weights=w_urgency)
        assert ranked[0]["urgency"] == 5

        # Impact-only weights → high-impact first
        w_impact = {"urgency_weight": 0.0, "impact_weight": 1.0, "freshness_weight": 0.0,
                    "freshness_decay_per_day": 0.1}
        ranked2 = rank_signals(signals, weights=w_impact)
        assert ranked2[0]["impact"] == 5


# ---------------------------------------------------------------------------
# partition_signals
# ---------------------------------------------------------------------------

class TestPartitionSignals:
    def _high(self) -> dict:
        return {"urgency": 5, "impact": 5, "age_days": 0}  # score ≈ 1.0 → promoted

    def _low(self) -> dict:
        return {"urgency": 1, "impact": 1, "age_days": 99}  # score near 0 → suppressed

    def _mid(self) -> dict:
        return {"urgency": 3, "impact": 3, "age_days": 5}  # score ≈ 0.46 → normal

    def test_three_buckets(self):
        """One signal per bucket under default thresholds."""
        promoted, normal, suppressed = partition_signals(
            [self._high(), self._mid(), self._low()],
            suppress_below=_DEFAULT_SUPPRESS_BELOW,
            promote_above=_DEFAULT_PROMOTE_ABOVE,
        )
        assert len(promoted) == 1
        assert len(normal) == 1
        assert len(suppressed) == 1

    def test_promoted_score_threshold(self):
        """All promoted entries score >= promote_above."""
        promoted, _, _ = partition_signals(
            [self._high(), self._mid(), self._low()],
            promote_above=0.8,
        )
        assert all(s["_score"] >= 0.8 for s in promoted)

    def test_suppressed_score_threshold(self):
        """All suppressed entries score < suppress_below."""
        _, _, suppressed = partition_signals(
            [self._high(), self._mid(), self._low()],
            suppress_below=0.2,
        )
        assert all(s["_score"] < 0.2 for s in suppressed)

    def test_empty_input(self):
        """Empty input produces three empty lists."""
        p, n, s = partition_signals([])
        assert p == [] and n == [] and s == []

    def test_score_key_present(self):
        """Returned signal dicts have _score key."""
        p, n, s = partition_signals([self._high(), self._mid(), self._low()])
        for bucket in (p, n, s):
            for sig in bucket:
                assert "_score" in sig

    def test_explicit_threshold_overrides(self):
        """Explicit suppress/promote thresholds override defaults."""
        # Setting promote_above=0.0 should make everything promoted
        p, n, s = partition_signals([self._mid()], promote_above=0.0, suppress_below=0.0)
        assert len(p) == 1
        assert len(n) == 0
        assert len(s) == 0

    def test_no_suppressed_when_suppress_below_zero(self):
        """With suppress_below=0.0, nothing is suppressed."""
        _, _, suppressed = partition_signals(
            [self._high(), self._mid(), self._low()],
            suppress_below=0.0,
            promote_above=1.1,  # nothing promoted either
        )
        assert suppressed == []
