#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/middleware/guardrails.py — Agent pipeline guardrails for Artha.

Provides a unified violation contract across the Artha pipeline.  Each
guardrail wraps an **existing** enforcement point (pii_guard.py,
vault_guard.py, injection_detector.py, trust_enforcer.py,
action_rate_limiter.py) rather than re-implementing the gate logic.

Architecture:
    Input guardrails  — run before the step with raw data in
    Tool guardrails   — run before external connector invocations
    Output guardrails — run on the step output before delivery

Wave 0 gate (AFW-1 Review Decision 5):
    ALL guardrails log a WARNING and continue (never block) when
    ``harness.wave0.complete`` is false.  Guardrails over a bypassed
    write path provide misleading safety coverage.

Ref: specs/agent-fw.md §3.1 (AFW-1)
"""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class GuardrailViolation(Exception):
    """Non-recoverable guardrail failure.

    Raised by output guardrails when a safety-critical leak cannot be
    remediated (e.g. PII that cannot be redacted).  Callers MUST NOT
    catch this silently — it signals that the current step output is
    unsafe for delivery.
    """

    def __init__(self, guardrail_name: str, message: str) -> None:
        super().__init__(f"[{guardrail_name}] {message}")
        self.guardrail_name = guardrail_name


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class GuardrailMode(Enum):
    """Execution mode for a guardrail in the registry."""
    BLOCKING = "blocking"
    PARALLEL = "parallel"


class TripwireResult(Enum):
    """Outcome of a guardrail check."""
    PASS = "pass"
    HALT = "halt"
    REDACT = "redact"
    FALLBACK = "fallback"


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------

@dataclass
class GuardrailOutput:
    """Result returned by ``BaseGuardrail.check()``."""
    result: TripwireResult
    message: str | None = None
    modified_data: Any = field(default=None)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class BaseGuardrail(ABC):
    """Abstract base for all Artha guardrails.

    Subclasses MUST override ``check()``.

    Wave 0 semantics: call ``_wave0_ok()`` at the top of every
    ``check()`` implementation.  If it returns False, emit a WARNING
    and return ``GuardrailOutput(TripwireResult.PASS)`` immediately.
    """

    name: str = "base_guardrail"
    mode: GuardrailMode = GuardrailMode.BLOCKING

    # Lazy-loaded logger — avoids circular imports during test collection.
    _log: Any = None

    def _get_log(self) -> Any:
        if self._log is None:
            try:
                from lib.logger import get_logger  # noqa: PLC0415
                type(self)._log = get_logger("guardrails")
            except Exception:  # noqa: BLE001
                type(self)._log = None
        return self._log

    def _wave0_ok(self) -> bool:
        """Return True if wave0 is complete.

        If wave0 is not complete, emit a WARNING and return False.
        The caller should then return PASS immediately.
        """
        try:
            from context_offloader import load_harness_flag  # noqa: PLC0415
            if not load_harness_flag("wave0.complete"):
                log = self._get_log()
                msg = (
                    f"[{self.name}] Wave 0 not complete "
                    "(harness.wave0.complete=false) — guardrail bypassed. "
                    "Activate guardrails only after all write-path bypass "
                    "writers are migrated."
                )
                if log is not None:
                    log.warning("guardrail.wave0_bypass", guardrail=self.name)
                else:
                    print(f"[WARN] {msg}", file=sys.stderr)
                return False
        except Exception:  # noqa: BLE001
            # If we can't load the flag, fail open (Wave 0 guard is advisory).
            pass
        return True

    @abstractmethod
    def check(self, context: dict, data: Any) -> GuardrailOutput:
        """Run the guardrail check.

        Args:
            context: Mutable runtime context dict for the current session.
                     Guardrails may read ``context["connector_errors"]``,
                     ``context["q_score"]``, etc.
            data:    The data being guarded (input text, output dict, etc.).

        Returns:
            GuardrailOutput — callers lift ``GuardrailViolation`` themselves.
        """
        raise NotImplementedError  # pragma: no cover


# ---------------------------------------------------------------------------
# Input guardrails
# ---------------------------------------------------------------------------

class VaultAccessGuardrail(BaseGuardrail):
    """Assert vault is decrypted before domain reads.

    Wraps ``vault_guard.check_file_readable()`` — does NOT re-implement
    the vault check logic.
    """

    name = "vault_access"
    mode = GuardrailMode.BLOCKING

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        # data may be a file path string or a domain name; context may carry
        # "domain_path" for the file being accessed.
        filepath = None
        if isinstance(data, str) and data:
            filepath = data
        elif isinstance(context, dict):
            filepath = context.get("domain_path") or context.get("filepath")

        if not filepath:
            # No path to check — pass through.
            return GuardrailOutput(TripwireResult.PASS)

        try:
            from vault_guard import check_file_readable  # noqa: PLC0415
            result = check_file_readable(str(filepath))
            if not result.get("readable", True):
                reason = result.get("reason", "locked")
                hint = result.get("hint", "Run: python scripts/vault.py decrypt")
                return GuardrailOutput(
                    TripwireResult.HALT,
                    message=f"🔒 Vault required: {reason}. {hint}",
                )
        except ImportError:
            pass  # vault_guard unavailable in minimal env — pass through

        return GuardrailOutput(TripwireResult.PASS)


class PromptInjectionGuardrail(BaseGuardrail):
    """Detect prompt injection in inbound text.

    Thin adapter wrapping ``InjectionDetector().scan()`` — does NOT
    re-implement injection detection.
    """

    name = "prompt_injection"
    mode = GuardrailMode.BLOCKING

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        text = data if isinstance(data, str) else str(data or "")

        try:
            from lib.injection_detector import InjectionDetector  # noqa: PLC0415
            result = InjectionDetector().scan(text)
            if result.injection_detected:
                signal_types = ", ".join(
                    s.signal_type for s in result.signals[:5]
                )
                return GuardrailOutput(
                    TripwireResult.HALT,
                    message=(
                        f"Prompt injection detected (signals: {signal_types}). "
                        "Response discarded."
                    ),
                )
        except ImportError:
            pass  # injection_detector unavailable — fail open

        return GuardrailOutput(TripwireResult.PASS)


# ---------------------------------------------------------------------------
# Tool guardrails
# ---------------------------------------------------------------------------

class RateLimitGuardrail(BaseGuardrail):
    """Check action budget before connector invocation.

    Wraps ``ActionRateLimiter.check()`` — delegates rate-limit logic.
    """

    name = "rate_limit"
    mode = GuardrailMode.BLOCKING

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        action_type = (
            context.get("action_type")
            if isinstance(context, dict)
            else None
        )
        if not action_type:
            return GuardrailOutput(TripwireResult.PASS)

        try:
            from action_rate_limiter import ActionRateLimiter, RateLimitError  # noqa: PLC0415
        except ImportError:
            return GuardrailOutput(TripwireResult.PASS)  # not installed — fail open

        try:
            ActionRateLimiter().check(action_type)
        except RateLimitError as exc:
            # RateLimitError is the expected policy-halt signal
            return GuardrailOutput(
                TripwireResult.HALT,
                message=f"Rate limit exceeded for {action_type!r}: {exc}",
            )
        except Exception:  # noqa: BLE001 — instantiation or unexpected error — fail open
            pass

        return GuardrailOutput(TripwireResult.PASS)


class ConnectorHealthGuardrail(BaseGuardrail):
    """Route to fallback when a connector is failing repeatedly.

    Checks ``context["connector_errors"]`` (dict: connector → error count).
    If any connector exceeds ``max_failures`` (default 3), returns FALLBACK.
    """

    name = "connector_health"
    mode = GuardrailMode.BLOCKING

    def __init__(self, max_failures: int = 3) -> None:
        self._max_failures = max_failures

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        connector_errors: dict[str, int] = (
            context.get("connector_errors", {})
            if isinstance(context, dict)
            else {}
        )
        for connector, count in connector_errors.items():
            if count > self._max_failures:
                return GuardrailOutput(
                    TripwireResult.FALLBACK,
                    message=(
                        f"Connector {connector!r} has failed {count} times "
                        f"(threshold: {self._max_failures}). "
                        "Routing to Cowork fallback."
                    ),
                )

        return GuardrailOutput(TripwireResult.PASS)


# ---------------------------------------------------------------------------
# Output guardrails
# ---------------------------------------------------------------------------

class PiiOutputGuardrail(BaseGuardrail):
    """Scan pipeline output for PII leaks.

    Uses ``pii_guard.scan()`` (in-process Python call, NOT subprocess).

    If PII is detected:
    - Attempts redaction via ``pii_guard.filter_text()``.
    - If redacted text still contains PII → raises ``GuardrailViolation``
      (non-recoverable; step output must be discarded entirely).
    - If PII was fully redacted → returns REDACT with modified_data = cleaned.

    Review Decision 2 compliance: unredactable leaks raise GuardrailViolation,
    NOT a REDACT result.
    Review Decision 6 compliance: pii_guard.py is called in-process (no
    subprocess-level containment claims).
    """

    name = "pii_output"
    mode = GuardrailMode.BLOCKING

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        text = data if isinstance(data, str) else str(data or "")

        try:
            import pii_guard  # noqa: PLC0415
            pii_found, found_types = pii_guard.scan(text)
            if not pii_found:
                return GuardrailOutput(TripwireResult.PASS)

            # Attempt redaction
            filtered, still_found = pii_guard.filter_text(text)

            # Verify the redacted output is clean
            still_pii, residual = pii_guard.scan(filtered)
            if still_pii:
                # Unredactable leak — hard HALT via exception (Review Decision 2)
                raise GuardrailViolation(
                    self.name,
                    f"Unredactable PII in output: {list(residual.keys())}. "
                    "Step output discarded.",
                )

            # PII was fully redacted — return modified data
            return GuardrailOutput(
                TripwireResult.REDACT,
                message=f"PII redacted from output: {list(found_types.keys())}",
                modified_data=filtered,
            )

        except GuardrailViolation:
            raise  # never swallow — propagate to caller
        except ImportError:
            pass  # pii_guard unavailable in minimal env — fail open

        return GuardrailOutput(TripwireResult.PASS)


class DataQualityGuardrail(BaseGuardrail):
    """Halt delivery of low-quality signals.

    Reads ``q_score`` from ``data`` (as dict with key ``"q_score"``) or
    ``context`` (key ``"q_score"``).  Signals below the configured threshold
    (default: 0.5) are halted before delivery.
    """

    name = "data_quality"
    mode = GuardrailMode.BLOCKING

    def __init__(self, q_score_threshold: float = 0.5) -> None:
        self._threshold = q_score_threshold

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        q_score: float | None = None

        if isinstance(data, dict):
            q_score = data.get("q_score")
        if q_score is None and isinstance(context, dict):
            q_score = context.get("q_score")

        if q_score is None:
            # No Q-score available — cannot evaluate, pass through.
            return GuardrailOutput(TripwireResult.PASS)

        if q_score < self._threshold:
            return GuardrailOutput(
                TripwireResult.HALT,
                message=(
                    f"Signal Q-score {q_score:.3f} is below threshold "
                    f"{self._threshold:.3f}. Signal dropped."
                ),
            )

        return GuardrailOutput(TripwireResult.PASS)


class FrictionGateGuardrail(BaseGuardrail):
    """Enforce approval for high-friction actions.

    Delegates to ``TrustEnforcer.check()`` — does NOT re-implement the
    4-point structural gate (autonomy floor, trust level, friction, L0
    observation-only).  Review Decision 1 compliance.

    Expects ``context["proposal"]``, ``context["action_config"]``, and
    optionally ``context["approved_by"]``.

    If TrustEnforcer returns ok=False, yields HALT.
    """

    name = "friction_gate"
    mode = GuardrailMode.BLOCKING

    def __init__(self, artha_dir: Any = None) -> None:
        self._artha_dir = artha_dir

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        proposal = context.get("proposal") if isinstance(context, dict) else None
        action_config = (
            context.get("action_config") if isinstance(context, dict) else None
        )

        if proposal is None or action_config is None:
            # Insufficient gate parameters — pass through with advisory note.
            return GuardrailOutput(TripwireResult.PASS)

        approved_by = (
            context.get("approved_by") if isinstance(context, dict) else None
        )

        try:
            from lib.common import ARTHA_DIR  # noqa: PLC0415
            from trust_enforcer import TrustEnforcer  # noqa: PLC0415

            artha_dir = self._artha_dir or ARTHA_DIR
            enforcer = TrustEnforcer(artha_dir)
            ok, reason = enforcer.check(proposal, action_config, approved_by)
            if not ok:
                return GuardrailOutput(
                    TripwireResult.HALT,
                    message=f"Friction gate blocked action: {reason}",
                )
        except ImportError:
            pass  # trust_enforcer unavailable — fail open

        return GuardrailOutput(TripwireResult.PASS)


# ---------------------------------------------------------------------------
# Registry of available guardrail classes (used by guardrail_registry.py)
# ---------------------------------------------------------------------------

GUARDRAIL_CLASSES: dict[str, type[BaseGuardrail]] = {
    "VaultAccessGuardrail": VaultAccessGuardrail,
    "PromptInjectionGuardrail": PromptInjectionGuardrail,
    "RateLimitGuardrail": RateLimitGuardrail,
    "ConnectorHealthGuardrail": ConnectorHealthGuardrail,
    "PiiOutputGuardrail": PiiOutputGuardrail,
    "DataQualityGuardrail": DataQualityGuardrail,
    "FrictionGateGuardrail": FrictionGateGuardrail,
}

__all__ = [
    "GuardrailViolation",
    "GuardrailMode",
    "TripwireResult",
    "GuardrailOutput",
    "BaseGuardrail",
    "VaultAccessGuardrail",
    "PromptInjectionGuardrail",
    "RateLimitGuardrail",
    "ConnectorHealthGuardrail",
    "PiiOutputGuardrail",
    "DataQualityGuardrail",
    "FrictionGateGuardrail",
    "GUARDRAIL_CLASSES",
]
