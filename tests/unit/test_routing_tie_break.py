"""tests/unit/test_routing_tie_break.py — DEBT-032: routing tie-break disambiguation."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))

from lib.agent_router import (  # type: ignore[import]
    RoutingMatch,
    RoutingResult,
    _emit_routing_margin,
    _AMBIGUITY_THRESHOLD,
)


def _make_match(name: str, confidence: float) -> RoutingMatch:
    return RoutingMatch(
        agent_name=name,
        confidence=confidence,
        keyword_hits=1,
        keyword_coverage=0.5,
        query_coverage=0.5,
        domain_bonus=0.0,
        recency_bonus=0.0,
        matched_keywords=["test"],
    )


class TestAmbiguityThreshold:
    def test_threshold_is_005(self):
        assert _AMBIGUITY_THRESHOLD == pytest.approx(0.05)


class TestEmitRoutingMargin:
    """_emit_routing_margin returns True iff margin < 0.05."""

    def _call(self, candidates):
        with patch("lib.metrics_writer.write_routing_margin"):
            return _emit_routing_margin(candidates, routing_ms=1.0)

    def test_no_candidates_returns_false(self):
        result = _emit_routing_margin([], routing_ms=0.0)
        assert result is False

    def test_single_candidate_no_ambiguity(self):
        candidates = [_make_match("agent_a", 0.90)]
        result = self._call(candidates)
        assert result is False

    def test_clear_winner_no_ambiguity(self):
        """margin = 0.90 - 0.60 = 0.30 → not ambiguous."""
        candidates = [_make_match("agent_a", 0.90), _make_match("agent_b", 0.60)]
        result = self._call(candidates)
        assert result is False

    def test_tie_triggers_ambiguity(self):
        """margin = 0.80 - 0.80 = 0.00 < 0.05 → ambiguous."""
        candidates = [_make_match("agent_a", 0.80), _make_match("agent_b", 0.80)]
        result = self._call(candidates)
        assert result is True

    def test_near_tie_triggers_ambiguity(self):
        """margin = 0.80 - 0.76 = 0.04 < 0.05 → ambiguous."""
        candidates = [_make_match("agent_a", 0.80), _make_match("agent_b", 0.76)]
        result = self._call(candidates)
        assert result is True

    def test_exactly_at_threshold_not_ambiguous(self):
        """margin = 0.80 - 0.75 = 0.05, NOT < 0.05 → not ambiguous."""
        candidates = [_make_match("agent_a", 0.80), _make_match("agent_b", 0.75)]
        result = self._call(candidates)
        assert result is False


class TestRoutingResultAmbiguityField:
    """RoutingResult carries routing_ambiguity field (DEBT-032)."""

    def test_default_is_false(self):
        result = RoutingResult(match=None, all_candidates=[], routing_ms=0.0)
        assert result.routing_ambiguity is False

    def test_can_be_set_true(self):
        m = _make_match("agent_a", 0.80)
        result = RoutingResult(
            match=m,
            all_candidates=[m],
            routing_ms=1.0,
            routing_ambiguity=True,
        )
        assert result.routing_ambiguity is True
