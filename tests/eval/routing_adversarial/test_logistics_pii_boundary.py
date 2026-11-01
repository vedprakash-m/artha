# pii-guard: ignore-file
# Reason: contains synthetic PII fixtures (fake SSN, fake names) used to validate
# that LogisticsPIIBoundaryGR correctly detects and halts on sensitive content.
# All values are fabricated test data — no real personal information.
"""
tests/eval/routing_adversarial/test_logistics_pii_boundary.py

Anti-golden fixture: Logistics domain — LogisticsPIIBoundaryGR
Ref: specs/prd-reloaded.md §9.2 (B3.2), §8.2, §8.3

ANTI-GOLDEN CONTRACT:
  When a Logistics proposal includes PII-classified content in the broker
  API payload, LogisticsPIIBoundaryGR must produce TripwireResult.HALT.
  Sensitivity classification above LOW must be blocked.

This test MUST FAIL until LogisticsPIIBoundaryGR is implemented in
scripts/middleware/guardrails.py (Todo 14).
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Anti-golden: LogisticsPIIBoundaryGR — PII blocked from broker payload
# ---------------------------------------------------------------------------

class TestLogisticsPIIBoundaryGuardrail:
    """LogisticsPIIBoundaryGR blocks proposals where PII sensitivity > LOW."""

    def test_class_exists(self):
        """Guardrail class must be importable from middleware.guardrails."""
        from middleware.guardrails import LogisticsPIIBoundaryGR  # noqa: F401

    def test_halt_when_pii_in_broker_payload(self):
        """HALT when scrubber.classify() returns sensitivity above LOW.

        Anti-golden: this test must FAIL until LogisticsPIIBoundaryGR is
        implemented. Once implemented, it must pass with HALT result.
        """
        from middleware.guardrails import LogisticsPIIBoundaryGR, TripwireResult
        from unittest.mock import MagicMock, patch

        gr = LogisticsPIIBoundaryGR()
        # Simulate scrubber classifying content as HIGH sensitivity (contains PII)
        fake_scrubber = MagicMock()
        fake_scrubber.ContextScrubber.return_value.classify.return_value = "HIGH"
        ctx = {
            "proposal_type": "broker_api_request",
            "payload": "Name: John Doe, SSN: 123-45-6789, requesting insurance quote",
        }
        with patch.dict("sys.modules", {"lib.context_scrubber": fake_scrubber}):
            out = gr.check(ctx, None)
        assert out.result == TripwireResult.HALT, (
            "LogisticsPIIBoundaryGR must HALT when payload contains HIGH-sensitivity PII "
            f"— got {out.result}"
        )

    def test_pass_when_low_sensitivity(self):
        """PASS when scrubber classifies content as LOW sensitivity (no PII)."""
        from middleware.guardrails import LogisticsPIIBoundaryGR, TripwireResult
        from unittest.mock import MagicMock, patch

        gr = LogisticsPIIBoundaryGR()
        fake_scrubber = MagicMock()
        fake_scrubber.ContextScrubber.return_value.classify.return_value = "LOW"
        ctx = {
            "proposal_type": "broker_api_request",
            "payload": "policy type: auto, zip: 98101",
        }
        with patch.dict("sys.modules", {"lib.context_scrubber": fake_scrubber}):
            out = gr.check(ctx, None)
        assert out.result == TripwireResult.PASS, (
            f"LogisticsPIIBoundaryGR must PASS for LOW-sensitivity content — got {out.result}"
        )

    def test_halt_when_medium_sensitivity(self):
        """HALT when sensitivity is MEDIUM (above LOW threshold)."""
        from middleware.guardrails import LogisticsPIIBoundaryGR, TripwireResult
        from unittest.mock import MagicMock, patch

        gr = LogisticsPIIBoundaryGR()
        fake_scrubber = MagicMock()
        fake_scrubber.ContextScrubber.return_value.classify.return_value = "MEDIUM"
        ctx = {
            "proposal_type": "broker_api_request",
            "payload": "Partial address: 123 Main St",
        }
        with patch.dict("sys.modules", {"lib.context_scrubber": fake_scrubber}):
            out = gr.check(ctx, None)
        assert out.result == TripwireResult.HALT, (
            "LogisticsPIIBoundaryGR must HALT for MEDIUM-sensitivity content (only LOW allowed) "
            f"— got {out.result}"
        )
