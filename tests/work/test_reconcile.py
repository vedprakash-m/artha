"""tests/work/test_reconcile.py — Tests for scripts/work/reconcile.py

≥90% coverage target.
Tests: match_by_id Pass 1, reconcile_plan_to_actual two-pass (with mock LLM),
edge cases, Protocol conformance.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from work.reconcile import (
    PlannedItem,
    ActualItem,
    ReconcileResult,
    LLMMatcher,
    match_by_id,
    reconcile_plan_to_actual,
)


# ---------------------------------------------------------------------------
# Mock LLM matcher for unit tests — zero LLM dependency
# ---------------------------------------------------------------------------

class MockLLMMatcher:
    """In-memory matcher: matches planned and actual items by title substring."""

    def __init__(self, pairs: list[tuple[str, str]]):
        """pairs = [(planned_title_substr, actual_title_substr)]"""
        self.pairs = pairs

    def match(
        self,
        planned: List[PlannedItem],
        actual: List[ActualItem],
    ) -> List[Tuple[PlannedItem, ActualItem]]:
        matched = []
        used_actual = set()
        for p_substr, a_substr in self.pairs:
            p = next((p for p in planned if p_substr.lower() in p.title.lower()), None)
            a = next(
                (a for a in actual if a_substr.lower() in a.title.lower() and id(a) not in used_actual),
                None,
            )
            if p and a:
                matched.append((p, a))
                used_actual.add(id(a))
        return matched


# ---------------------------------------------------------------------------
# match_by_id — Pass 1
# ---------------------------------------------------------------------------

class TestMatchById:
    def test_empty_inputs(self):
        result = match_by_id([], [])
        assert result.matched == []
        assert result.unmatched_planned == []
        assert result.unmatched_actual == []

    def test_cf_id_match(self):
        p = PlannedItem("Deploy service", cf_id="CF-001")
        a = ActualItem("Deployed service v2", cf_id="CF-001", completed=True)
        result = match_by_id([p], [a])
        assert len(result.matched) == 1
        assert result.matched[0][0] is p
        assert result.matched[0][1] is a

    def test_task_id_match_when_no_cf_id(self):
        p = PlannedItem("ADO task", task_id="12345")
        a = ActualItem("ADO task completed", task_id="12345")
        result = match_by_id([p], [a])
        assert len(result.matched) == 1

    def test_cf_id_takes_precedence_over_task_id(self):
        p = PlannedItem("Item", cf_id="CF-001", task_id="999")
        a_by_cf = ActualItem("By CF", cf_id="CF-001")
        a_by_task = ActualItem("By task", task_id="999")
        result = match_by_id([p], [a_by_cf, a_by_task])
        assert len(result.matched) == 1
        assert result.matched[0][1] is a_by_cf

    def test_unmatched_planned_when_no_id_overlap(self):
        p = PlannedItem("Feature X", cf_id="CF-999")
        a = ActualItem("Feature X", cf_id="CF-000")
        result = match_by_id([p], [a])
        assert len(result.matched) == 0
        assert p in result.unmatched_planned
        assert a in result.unmatched_actual

    def test_planned_with_no_ids_never_matches(self):
        p = PlannedItem("Work item with no IDs")
        a = ActualItem("Work item with no IDs")  # Same title but no IDs
        result = match_by_id([p], [a])
        assert len(result.matched) == 0
        assert p in result.unmatched_planned
        assert a in result.unmatched_actual

    def test_each_actual_matched_at_most_once(self):
        """Two planned items with same cf_id should only match one actual."""
        p1 = PlannedItem("Item 1", cf_id="CF-001")
        p2 = PlannedItem("Item 2", cf_id="CF-001")  # Duplicate CF-ID
        a = ActualItem("Actual", cf_id="CF-001")
        result = match_by_id([p1, p2], [a])
        assert len(result.matched) == 1
        assert len(result.unmatched_planned) == 1

    def test_multiple_items_matched(self):
        items = [
            (PlannedItem(f"P{i}", cf_id=f"CF-{i:03d}"), ActualItem(f"A{i}", cf_id=f"CF-{i:03d}"))
            for i in range(5)
        ]
        planned = [p for p, _ in items]
        actual = [a for _, a in items]
        result = match_by_id(planned, actual)
        assert len(result.matched) == 5
        assert result.unmatched_planned == []
        assert result.unmatched_actual == []


# ---------------------------------------------------------------------------
# reconcile_plan_to_actual — two-pass
# ---------------------------------------------------------------------------

class TestReconcilePlanToActual:
    def test_no_llm_uses_id_match_only(self):
        p = PlannedItem("P1", cf_id="CF-001")
        a = ActualItem("A1", cf_id="CF-001")
        result = reconcile_plan_to_actual([p], [a], llm_matcher=None)
        assert len(result.matched) == 1

    def test_llm_matcher_applied_on_remaining(self):
        p_id = PlannedItem("Matched by ID", cf_id="CF-001")
        p_fuzzy = PlannedItem("Deploy microservice alpha")
        a_id = ActualItem("Done by ID", cf_id="CF-001")
        a_fuzzy = ActualItem("Deployed microservice alpha to prod")

        matcher = MockLLMMatcher([("microservice alpha", "microservice alpha")])
        result = reconcile_plan_to_actual([p_id, p_fuzzy], [a_id, a_fuzzy], matcher)

        assert len(result.matched) == 2
        assert result.unmatched_planned == []
        assert result.unmatched_actual == []

    def test_llm_not_called_when_all_matched_by_id(self):
        """If Pass 1 matches everything, Pass 2 should have nothing to process."""
        p = PlannedItem("P", cf_id="CF-001")
        a = ActualItem("A", cf_id="CF-001")

        class FailingMatcher:
            def match(self, planned, actual):
                raise AssertionError("LLM should not be called when all matched by ID")

        result = reconcile_plan_to_actual([p], [a], FailingMatcher())
        assert len(result.matched) == 1

    def test_llm_not_called_when_no_unmatched_remain(self):
        """Empty lists → LLM match() returns empty → no crash."""
        matcher = MockLLMMatcher([])
        result = reconcile_plan_to_actual([], [], matcher)
        assert result.matched == []

    def test_unmatched_actual_items_preserved(self):
        p = PlannedItem("P1", cf_id="CF-001")
        a1 = ActualItem("A1", cf_id="CF-001")
        a2 = ActualItem("Unplanned work")  # No ID, no fuzzy match
        result = reconcile_plan_to_actual([p], [a1, a2], llm_matcher=None)
        assert a2 in result.unmatched_actual

    def test_returns_reconcile_result_type(self):
        result = reconcile_plan_to_actual([], [])
        assert isinstance(result, ReconcileResult)


# ---------------------------------------------------------------------------
# ReconcileResult dataclass
# ---------------------------------------------------------------------------

class TestReconcileResult:
    def test_fields_accessible(self):
        result = ReconcileResult(
            matched=[],
            unmatched_planned=[PlannedItem("P")],
            unmatched_actual=[ActualItem("A")],
        )
        assert len(result.matched) == 0
        assert len(result.unmatched_planned) == 1
        assert len(result.unmatched_actual) == 1


# ---------------------------------------------------------------------------
# PlannedItem and ActualItem defaults
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_planned_item_defaults(self):
        p = PlannedItem("Task")
        assert p.cf_id is None
        assert p.task_id is None

    def test_actual_item_defaults(self):
        a = ActualItem("Done")
        assert a.cf_id is None
        assert a.task_id is None
        assert a.completed is False

    def test_actual_item_completed_true(self):
        a = ActualItem("Done", completed=True)
        assert a.completed is True
