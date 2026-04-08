"""
tests/eval/routing_adversarial/test_tribe_rate_limit.py

Anti-golden fixture: Tribe domain — TribeRateLimitGR
Ref: specs/prd-reloaded.md §9.2 (B3.2), §8.2

ANTI-GOLDEN CONTRACT:
  TribeRateLimitGR must enforce a hard cap of 5 catch-up drafts per session.
  The 6th+ draft must be silently dropped (HALT result) and the event logged.

This test MUST FAIL until TribeRateLimitGR is implemented in
scripts/middleware/guardrails.py (Todo 14).
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Anti-golden: TribeRateLimitGR — hard cap of 5 drafts per catch-up
# ---------------------------------------------------------------------------

class TestTribeRateLimitGuardrail:
    """TribeRateLimitGR silently drops the 6th+ draft in a catch-up session."""

    def test_class_exists(self):
        """Guardrail class must be importable from middleware.guardrails."""
        from middleware.guardrails import TribeRateLimitGR  # noqa: F401

    def test_halt_on_sixth_draft(self):
        """HALT (silent drop) when draft_count >= 5 (6th+ draft blocked).

        Anti-golden: this test must FAIL until TribeRateLimitGR is
        implemented. Once implemented, it must pass with HALT result.
        """
        from middleware.guardrails import TribeRateLimitGR, TripwireResult

        gr = TribeRateLimitGR()
        ctx = {
            "domain": "tribe",
            "action": "draft_message",
            "session_draft_count": 5,  # 5 already sent; this would be the 6th
        }
        out = gr.check(ctx, None)
        assert out.result == TripwireResult.HALT, (
            "TribeRateLimitGR must HALT (silent drop) when session_draft_count >= 5 "
            f"— got {out.result}"
        )

    def test_pass_on_fifth_draft(self):
        """PASS when draft_count is 4 (5th draft is still within cap)."""
        from middleware.guardrails import TribeRateLimitGR, TripwireResult

        gr = TribeRateLimitGR()
        ctx = {
            "domain": "tribe",
            "action": "draft_message",
            "session_draft_count": 4,  # 4 already sent; this would be the 5th
        }
        out = gr.check(ctx, None)
        assert out.result == TripwireResult.PASS, (
            f"TribeRateLimitGR must PASS for the 5th draft (count=4) — got {out.result}"
        )

    def test_pass_on_first_draft(self):
        """PASS when no drafts have been sent yet (cold start)."""
        from middleware.guardrails import TribeRateLimitGR, TripwireResult

        gr = TribeRateLimitGR()
        ctx = {
            "domain": "tribe",
            "action": "draft_message",
            "session_draft_count": 0,
        }
        out = gr.check(ctx, None)
        assert out.result == TripwireResult.PASS, (
            f"TribeRateLimitGR must PASS for the first draft — got {out.result}"
        )

    def test_non_draft_action_not_counted(self):
        """Non-draft actions (e.g., display_summary) are not subject to rate cap."""
        from middleware.guardrails import TribeRateLimitGR, TripwireResult

        gr = TribeRateLimitGR()
        ctx = {
            "domain": "tribe",
            "action": "display_summary",
            "session_draft_count": 10,  # many drafts, but no new draft action
        }
        out = gr.check(ctx, None)
        assert out.result == TripwireResult.PASS, (
            "TribeRateLimitGR must PASS for non-draft-message actions regardless of count "
            f"— got {out.result}"
        )
