"""
tests/eval/routing_adversarial/test_capital_amount_confirm.py

Anti-golden fixture: Capital domain — CapitalAmountConfirmGR
Ref: specs/prd-reloaded.md §9.2 (B3.2), §8.2, §8.4

ANTI-GOLDEN CONTRACT:
  When a Capital proposal exceeds $200, CapitalAmountConfirmGR must require
  amount-confirmation retyping. If confirmation is absent or mismatched,
  the guard must produce TripwireResult.HALT.

This test MUST FAIL until CapitalAmountConfirmGR is implemented in
scripts/middleware/guardrails.py (Todo 14).
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Anti-golden: CapitalAmountConfirmGR — >$200 requires amount confirmation
# ---------------------------------------------------------------------------

class TestCapitalAmountConfirmGuardrail:
    """CapitalAmountConfirmGR requires retyped amount confirmation for >$200 proposals."""

    def test_class_exists(self):
        """Guardrail class must be importable from middleware.guardrails."""
        from middleware.guardrails import CapitalAmountConfirmGR  # noqa: F401

    def test_halt_when_large_amount_without_confirmation(self):
        """HALT when proposal amount >$200 and no confirmation_amount provided.

        Anti-golden: this test must FAIL until CapitalAmountConfirmGR is
        implemented. Once implemented, it must pass with HALT result.
        """
        from middleware.guardrails import CapitalAmountConfirmGR, TripwireResult

        gr = CapitalAmountConfirmGR()
        ctx = {
            "domain": "capital",
            "proposal_amount": 500.00,
            # confirmation_amount absent — user has not retyped the amount
        }
        out = gr.check(ctx, None)
        assert out.result == TripwireResult.HALT, (
            "CapitalAmountConfirmGR must HALT when proposal_amount >$200 and "
            f"confirmation_amount is absent — got {out.result}"
        )

    def test_halt_when_confirmation_mismatches(self):
        """HALT when confirmation_amount does not match proposal_amount (typo/fraud)."""
        from middleware.guardrails import CapitalAmountConfirmGR, TripwireResult

        gr = CapitalAmountConfirmGR()
        ctx = {
            "domain": "capital",
            "proposal_amount": 500.00,
            "confirmation_amount": 50.00,  # mismatch: user typed wrong amount
        }
        out = gr.check(ctx, None)
        assert out.result == TripwireResult.HALT, (
            "CapitalAmountConfirmGR must HALT when confirmation_amount mismatches "
            f"proposal_amount — got {out.result}"
        )

    def test_pass_when_confirmation_matches(self):
        """PASS when confirmation_amount matches proposal_amount exactly."""
        from middleware.guardrails import CapitalAmountConfirmGR, TripwireResult

        gr = CapitalAmountConfirmGR()
        ctx = {
            "domain": "capital",
            "proposal_amount": 500.00,
            "confirmation_amount": 500.00,
        }
        out = gr.check(ctx, None)
        assert out.result == TripwireResult.PASS, (
            "CapitalAmountConfirmGR must PASS when confirmation matches — "
            f"got {out.result}"
        )

    def test_pass_when_amount_at_or_below_threshold(self):
        """PASS when proposal amount is ≤$200 (no confirmation required)."""
        from middleware.guardrails import CapitalAmountConfirmGR, TripwireResult

        gr = CapitalAmountConfirmGR()
        # No confirmation_amount provided but amount is within threshold
        ctx = {
            "domain": "capital",
            "proposal_amount": 150.00,
        }
        out = gr.check(ctx, None)
        assert out.result == TripwireResult.PASS, (
            "CapitalAmountConfirmGR must PASS for amounts ≤$200 without confirmation "
            f"— got {out.result}"
        )

    def test_pass_at_exact_threshold(self):
        """PASS at exactly $200 (threshold is >$200, not ≥$200)."""
        from middleware.guardrails import CapitalAmountConfirmGR, TripwireResult

        gr = CapitalAmountConfirmGR()
        ctx = {
            "domain": "capital",
            "proposal_amount": 200.00,
        }
        out = gr.check(ctx, None)
        assert out.result == TripwireResult.PASS, (
            "CapitalAmountConfirmGR must PASS at exactly $200 (threshold is strictly >$200) "
            f"— got {out.result}"
        )
