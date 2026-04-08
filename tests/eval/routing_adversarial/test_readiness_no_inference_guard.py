"""
tests/eval/routing_adversarial/test_readiness_no_inference_guard.py

Anti-golden fixture: Readiness domain — ReadinessNoInferenceGR
Ref: specs/prd-reloaded.md §9.2 (B3.2), §8.2

ANTI-GOLDEN CONTRACT:
  When readiness_score is unknown (Apple Health export missing or stale >24h)
  AND a proposal attempts to write a calendar restructuring action,
  ReadinessNoInferenceGR must produce TripwireResult.HALT.

This test MUST FAIL until ReadinessNoInferenceGR is implemented in
scripts/middleware/guardrails.py (Todo 14).
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Anti-golden: ReadinessNoInferenceGR — calendar write blocked when unknown
# ---------------------------------------------------------------------------

class TestReadinessNoInferenceGuardrail:
    """ReadinessNoInferenceGR blocks calendar writes when readiness_score unknown."""

    def test_class_exists(self):
        """Guardrail class must be importable from middleware.guardrails."""
        from middleware.guardrails import ReadinessNoInferenceGR  # noqa: F401

    def test_halt_on_calendar_write_with_unknown_score(self):
        """HALT when proposal writes to calendar and readiness_score is unknown.

        Anti-golden: this test must FAIL until ReadinessNoInferenceGR is
        implemented. Once implemented, it must pass with HALT result.
        """
        from middleware.guardrails import ReadinessNoInferenceGR, TripwireResult

        gr = ReadinessNoInferenceGR()
        ctx = {
            "readiness_score": "unknown",
            "proposal_type": "calendar_restructure",
            "action": "write",
        }
        out = gr.check(ctx, None)
        assert out.result == TripwireResult.HALT, (
            "ReadinessNoInferenceGR must HALT calendar write when readiness_score "
            f"is unknown — got {out.result}"
        )

    def test_pass_when_score_known(self):
        """PASS when readiness_score is a known numeric value."""
        from middleware.guardrails import ReadinessNoInferenceGR, TripwireResult

        gr = ReadinessNoInferenceGR()
        ctx = {
            "readiness_score": 72,
            "proposal_type": "calendar_restructure",
            "action": "write",
        }
        out = gr.check(ctx, None)
        assert out.result == TripwireResult.PASS, (
            f"ReadinessNoInferenceGR must PASS when score is known — got {out.result}"
        )

    def test_pass_on_non_calendar_proposal(self):
        """PASS when proposal is not a calendar write (readiness display is fine)."""
        from middleware.guardrails import ReadinessNoInferenceGR, TripwireResult

        gr = ReadinessNoInferenceGR()
        ctx = {
            "readiness_score": "unknown",
            "proposal_type": "display_summary",
            "action": "read",
        }
        out = gr.check(ctx, None)
        assert out.result == TripwireResult.PASS, (
            f"ReadinessNoInferenceGR must PASS for non-write proposals — got {out.result}"
        )
