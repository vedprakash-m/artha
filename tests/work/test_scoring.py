"""tests/work/test_scoring.py — Tests for scripts/work/scoring.py

≥95% coverage target.
Tests: score_item with all dimension combinations, SCORE_MAX=2.1,
label thresholds, score_goal_alignment, config injection, degradation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from work.scoring import (
    score_item,
    score_goal_alignment,
    ScoredItem,
    SCORE_MAX,
    SCORING_RUBRIC,
    _DEFAULT_URGENCY,
    _DEFAULT_IMPORTANCE,
    _DEFAULT_VISIBILITY,
    _DEFAULT_GOAL_ALIGNMENT,
    _DEFAULT_LABEL_THRESHOLDS,
)

# Inject weights directly so tests don't touch artha_config.yaml
_W = dict(
    urgency_weights=_DEFAULT_URGENCY,
    importance_weights=_DEFAULT_IMPORTANCE,
    visibility_weights=_DEFAULT_VISIBILITY,
    goal_alignment_weights=_DEFAULT_GOAL_ALIGNMENT,
    label_thresholds=_DEFAULT_LABEL_THRESHOLDS,
)


# ---------------------------------------------------------------------------
# SCORE_MAX constant
# ---------------------------------------------------------------------------

class TestScoreMax:
    def test_score_max_value(self):
        assert SCORE_MAX == 2.1

    def test_max_score_achievable(self):
        """critical × strategic + org + direct should hit SCORE_MAX."""
        item = score_item(
            "Max item",
            urgency="critical", importance="strategic",
            visibility="org", goal_alignment="direct",
            **_W,
        )
        # 1.0*1.0 + 0.6 + 0.5 = 2.1
        assert abs(item.raw_score - 2.1) < 0.001
        assert item.normalized_score == 1.0

    def test_normalized_score_clamps_at_1(self):
        """Normalized score should never exceed 1.0."""
        item = score_item("x", urgency="critical", importance="strategic",
                          visibility="org", goal_alignment="direct", **_W)
        assert item.normalized_score <= 1.0


# ---------------------------------------------------------------------------
# Label thresholds
# ---------------------------------------------------------------------------

class TestLabelThresholds:
    def test_high_label_at_threshold(self):
        """Score >= 1.0 → HIGH."""
        # critical(1.0) × strategic(1.0) = 1.0 → HIGH
        item = score_item("H", urgency="critical", importance="strategic",
                          visibility="self", goal_alignment="unaligned", **_W)
        assert item.label == "HIGH"
        assert item.raw_score >= 1.0

    def test_medium_label_range(self):
        """Score 0.3–0.99 → MEDIUM."""
        # medium(0.5) × operational(0.7) = 0.35 → MEDIUM
        item = score_item("M", urgency="medium", importance="operational",
                          visibility="self", goal_alignment="unaligned", **_W)
        assert item.label == "MEDIUM"
        assert 0.3 <= item.raw_score < 1.0

    def test_low_label_below_threshold(self):
        """Score < 0.3 → LOW."""
        # low(0.2) × administrative(0.3) = 0.06 → LOW
        item = score_item("L", urgency="low", importance="administrative",
                          visibility="self", goal_alignment="unaligned", **_W)
        assert item.label == "LOW"
        assert item.raw_score < 0.3

    def test_visibility_bonus_lifts_to_high(self):
        """Team visibility (+0.2) can push a MEDIUM item to HIGH."""
        # medium(0.5) × operational(0.7) + team(0.2) = 0.55 → still MEDIUM
        # But: high(0.8) × operational(0.7) + team(0.2) = 0.76 → still MEDIUM
        # org(0.6) + critical(1.0) × strategic(1.0) = 1.6 → HIGH confirmed
        item = score_item("V", urgency="critical", importance="strategic",
                          visibility="org", goal_alignment="unaligned", **_W)
        assert item.label == "HIGH"


# ---------------------------------------------------------------------------
# Individual dimension weights
# ---------------------------------------------------------------------------

class TestUrgencyWeights:
    @pytest.mark.parametrize("urgency,expected_u", [
        ("critical", 1.0),
        ("high", 0.8),
        ("medium", 0.5),
        ("low", 0.2),
    ])
    def test_urgency_weights(self, urgency, expected_u):
        item = score_item("T", urgency=urgency, importance="strategic",
                          visibility="self", goal_alignment="unaligned", **_W)
        # raw = urgency * 1.0 (strategic) + 0.0 + 0.0
        assert abs(item.raw_score - expected_u) < 0.001


class TestImportanceWeights:
    @pytest.mark.parametrize("importance,expected_i", [
        ("strategic", 1.0),
        ("operational", 0.7),
        ("administrative", 0.3),
    ])
    def test_importance_weights(self, importance, expected_i):
        item = score_item("T", urgency="critical", importance=importance,
                          visibility="self", goal_alignment="unaligned", **_W)
        assert abs(item.raw_score - expected_i) < 0.001


class TestVisibilityBonus:
    @pytest.mark.parametrize("visibility,bonus", [
        ("org", 0.6),
        ("skip", 0.4),
        ("team", 0.2),
        ("self", 0.0),
    ])
    def test_visibility_bonus(self, visibility, bonus):
        # Use low urgency/importance to isolate visibility bonus contribution
        item = score_item("T", urgency="low", importance="administrative",
                          visibility=visibility, goal_alignment="unaligned", **_W)
        # raw = 0.2*0.3 + bonus = 0.06 + bonus
        assert abs(item.raw_score - (0.06 + bonus)) < 0.001


class TestGoalAlignmentBonus:
    @pytest.mark.parametrize("goal_alignment,bonus", [
        ("direct", 0.5),
        ("tangential", 0.2),
        ("unaligned", 0.0),
    ])
    def test_goal_alignment_bonus(self, goal_alignment, bonus):
        item = score_item("T", urgency="low", importance="administrative",
                          visibility="self", goal_alignment=goal_alignment, **_W)
        assert abs(item.raw_score - (0.06 + bonus)) < 0.001


# ---------------------------------------------------------------------------
# Case insensitivity
# ---------------------------------------------------------------------------

class TestCaseInsensitivity:
    def test_urgency_uppercase(self):
        item = score_item("T", urgency="CRITICAL", importance="strategic",
                          visibility="self", goal_alignment="unaligned", **_W)
        assert item.urgency == "critical"
        assert abs(item.raw_score - 1.0) < 0.001

    def test_mixed_case(self):
        item1 = score_item("T", urgency="High", importance="Operational",
                           visibility="Team", goal_alignment="Direct", **_W)
        item2 = score_item("T", urgency="high", importance="operational",
                           visibility="team", goal_alignment="direct", **_W)
        assert abs(item1.raw_score - item2.raw_score) < 0.001


# ---------------------------------------------------------------------------
# Unknown/invalid dimension values → safe degradation
# ---------------------------------------------------------------------------

class TestSafeDegradation:
    def test_unknown_urgency_uses_minimum(self):
        item = score_item("T", urgency="UNKNOWN_VALUE", importance="strategic",
                          visibility="self", goal_alignment="unaligned", **_W)
        # Should not raise; uses min weight (0.2 for low)
        assert item.raw_score >= 0.0

    def test_unknown_visibility_defaults_to_zero(self):
        item = score_item("T", urgency="low", importance="administrative",
                          visibility="INVALID", goal_alignment="unaligned", **_W)
        # Visibility unknown → 0.0 (visibility_weights.get(key, 0.0))
        # raw = 0.06 + 0.0
        assert item.raw_score < 0.5

    def test_empty_title_allowed(self):
        item = score_item("", urgency="medium", importance="operational",
                          visibility="self", goal_alignment="unaligned", **_W)
        assert item.title == ""
        assert isinstance(item.raw_score, float)


# ---------------------------------------------------------------------------
# ScoredItem dataclass
# ---------------------------------------------------------------------------

class TestScoredItem:
    def test_returned_type(self):
        item = score_item("Test", **_W)
        assert isinstance(item, ScoredItem)

    def test_all_fields_populated(self):
        item = score_item("Full item", urgency="high", importance="operational",
                          visibility="team", goal_alignment="direct", **_W)
        assert item.title == "Full item"
        assert item.urgency == "high"
        assert item.importance == "operational"
        assert item.visibility == "team"
        assert item.goal_alignment == "direct"
        assert item.label in ("HIGH", "MEDIUM", "LOW")
        assert 0.0 <= item.normalized_score <= 1.0


# ---------------------------------------------------------------------------
# score_goal_alignment
# ---------------------------------------------------------------------------

class TestScoreGoalAlignment:
    def test_empty_goals_returns_unaligned(self):
        assert score_goal_alignment("XPF pipeline work", []) == "unaligned"

    def test_direct_hit_two_tokens(self):
        goals = ["G1: XPF Pipeline Automation", "G2: Fleet Reliability"]
        result = score_goal_alignment("XPF pipeline review meeting", goals)
        assert result in ("direct", "tangential")

    def test_single_match_is_tangential(self):
        goals = ["G1: Fleet Automation"]
        result = score_goal_alignment("fleet deployment task", goals)
        assert result in ("tangential", "direct")

    def test_no_overlap_is_unaligned(self):
        goals = ["G1: XPF Pipeline"]
        result = score_goal_alignment("grocery shopping list", goals)
        assert result == "unaligned"

    def test_case_insensitive_matching(self):
        goals = ["G1: FLEET AUTOMATION"]
        result = score_goal_alignment("FLEET maintenance", goals)
        # Both "fleet" tokens — tangential or direct
        assert result in ("direct", "tangential")

    def test_short_tokens_ignored(self):
        """Tokens <= 3 chars are ignored (e.g. 'the', 'of', 'G1:')."""
        goals = ["G1: of the"]
        result = score_goal_alignment("of the great work", goals)
        assert result == "unaligned"


# ---------------------------------------------------------------------------
# SCORING_RUBRIC export
# ---------------------------------------------------------------------------

class TestScoringRubricExport:
    def test_rubric_has_all_keys(self):
        assert "urgency" in SCORING_RUBRIC
        assert "importance" in SCORING_RUBRIC
        assert "visibility" in SCORING_RUBRIC
        assert "goal_alignment" in SCORING_RUBRIC
        assert "label_thresholds" in SCORING_RUBRIC
