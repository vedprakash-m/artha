"""tests/unit/test_quality_gate.py — Tests for quality_gate.py. specs/steal.md §15.4.2"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"))

from quality_gate import GateResult, evaluate_gate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pass() -> bool:
    return True


def _fail() -> bool:
    return False


# ---------------------------------------------------------------------------
# Basic gate behaviour
# ---------------------------------------------------------------------------

class TestAllChecksPass:
    def test_returns_passed_true_when_all_checks_pass(self):
        result = evaluate_gate("test_gate", [_pass, _pass])
        assert result.passed is True

    def test_gate_name_propagated(self):
        result = evaluate_gate("post_fetch", [_pass])
        assert result.gate_name == "post_fetch"

    def test_attempt_is_one_on_first_success(self):
        result = evaluate_gate("test_gate", [_pass])
        assert result.attempt == 1

    def test_check_results_included(self):
        result = evaluate_gate("test_gate", [_pass, _pass])
        assert len(result.checks) == 2
        assert all(c["passed"] for c in result.checks)

    def test_empty_checks_list_passes(self):
        result = evaluate_gate("test_gate", [])
        assert result.passed is True


# ---------------------------------------------------------------------------
# Retry on failure
# ---------------------------------------------------------------------------

class TestRetryOnFailure:
    def test_gate_passes_on_second_attempt(self):
        call_count = {"n": 0}

        def flaky() -> bool:
            call_count["n"] += 1
            return call_count["n"] >= 2  # fail once, pass on retry

        result = evaluate_gate("test_gate", [flaky], max_retries=2)
        assert result.passed is True
        assert result.attempt == 2

    def test_max_retries_zero_no_retry(self):
        result = evaluate_gate("test_gate", [_fail], max_retries=0)
        assert result.passed is False
        assert result.attempt == 1

    def test_all_retries_exhausted_returns_failed(self):
        result = evaluate_gate("test_gate", [_fail], max_retries=2)
        assert result.passed is False
        assert result.attempt == 3  # initial + 2 retries


# ---------------------------------------------------------------------------
# Hard fail: telemetry emitted
# ---------------------------------------------------------------------------

class TestHardFailEmitsTelemetry:
    def test_gate_emits_telemetry_on_failure(self):
        """On hard fail, telemetry.emit('pipeline.gate_failed', ...) must be called."""
        mock_tel = MagicMock()
        # Patch the telemetry import inside quality_gate._emit_gate_failed
        with patch.dict("sys.modules", {"telemetry": mock_tel}):
            result = evaluate_gate("post_reason", [_fail], max_retries=0)

        assert result.passed is False
        mock_tel.emit.assert_called_once()
        call_args = mock_tel.emit.call_args
        assert call_args[0][0] == "pipeline.gate_failed"

    def test_telemetry_failure_does_not_propagate(self):
        """Even if telemetry.emit raises, the gate result must still be returned."""
        bad_tel = MagicMock()
        bad_tel.emit.side_effect = RuntimeError("telemetry broken")

        with patch.dict("sys.modules", {"telemetry": bad_tel}):
            result = evaluate_gate("test_gate", [_fail], max_retries=0)

        assert result.passed is False  # result returned despite telemetry error


# ---------------------------------------------------------------------------
# Exception in check function
# ---------------------------------------------------------------------------

class TestCheckExceptions:
    def test_exception_in_check_treated_as_failure(self):
        def boom() -> bool:
            raise RuntimeError("explosion")

        result = evaluate_gate("test_gate", [boom], max_retries=0)
        assert result.passed is False
        check = result.checks[0]
        assert check["passed"] is False
        assert "exception" in check["message"]
