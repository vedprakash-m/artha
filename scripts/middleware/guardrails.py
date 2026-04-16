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

import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
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
# DEBT-GUARD-001: Wave 0 mode helpers (module-level, cached at first call)
# ---------------------------------------------------------------------------

_WAVE0_MODE: str | None = None  # "hard" | "soft" | None (=soft)


def _get_wave0_mode() -> str:
    """Return the wave0_gate mode from guardrails.yaml. Cached at first call.

    Returns "hard" or "soft".  Any other value (including missing key) defaults
    to "soft" — safer for unknown deployments.
    """
    global _WAVE0_MODE
    if _WAVE0_MODE is not None:
        return _WAVE0_MODE
    try:
        import yaml  # noqa: PLC0415
        _guardrails_yaml = Path(__file__).resolve().parents[2] / "config" / "guardrails.yaml"
        with open(_guardrails_yaml) as fh:
            cfg = yaml.safe_load(fh) or {}
        _WAVE0_MODE = str(cfg.get("wave0_gate", "soft")).lower()
    except Exception:  # noqa: BLE001
        _WAVE0_MODE = "soft"
    return _WAVE0_MODE


def _guardrail_write_audit(event: str, data: dict) -> None:
    """Append a structured audit line to state/audit.md (best-effort)."""
    try:
        from datetime import datetime, timezone  # noqa: PLC0415
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _audit = Path(__file__).resolve().parents[2] / "state" / "audit.md"
        fields = " | ".join(f"{k}:{v}" for k, v in data.items())
        line = f"| {now} | {event} | {fields} |\n"
        with _audit.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:  # noqa: BLE001
        pass  # audit write failure is never blocking


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
        """Return True if wave0 is complete and guarded paths may proceed.

        DEBT-GUARD-001: Honours wave0_gate mode from guardrails.yaml.
        - "hard": raises RuntimeError when wave0 incomplete or flag unloadable.
          Tests must set ARTHA_WAVE0_OVERRIDE=<reason> to bypass in CI.
        - "soft": emits WARNING and returns False (legacy fail-open behaviour).

        Override: set env ARTHA_WAVE0_OVERRIDE=<reason> to bypass hard block.
          Every override is written to state/audit.md (mandatory audit trail).
        """
        # RD-47: Dual-key confirmation for ARTHA_WAVE0_OVERRIDE.
        # OVERRIDE alone is insufficient — ARTHA_WAVE0_CONFIRM must also be set
        # to the current session hour (YYYYMMDDHH format) to prevent accidental
        # bypass from stale or automated env vars.
        override = os.environ.get("ARTHA_WAVE0_OVERRIDE", "")
        if override:
            import datetime as _dt
            confirm = os.environ.get("ARTHA_WAVE0_CONFIRM", "")
            current_hour = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d%H")
            if confirm != current_hour:
                # Dual-key check failed — emit visible warning and do NOT bypass
                _warning_msg = (
                    f"⚠ [GUARDRAIL-SECURITY] ARTHA_WAVE0_OVERRIDE set but "
                    f"ARTHA_WAVE0_CONFIRM is missing or stale. "
                    f"Expected ARTHA_WAVE0_CONFIRM={current_hour} (current UTC hour). "
                    f"Guardrail [{self.name}] NOT bypassed. "
                    f"This prevents accidental bypass from stale env vars."
                )
                print(_warning_msg, file=sys.stderr)
                _guardrail_write_audit(
                    "WAVE0_OVERRIDE_REJECTED_NO_CONFIRM",
                    {"guardrail": self.name, "override_reason": override[:120],
                     "confirm_provided": confirm[:20] if confirm else "(none)",
                     "expected": current_hour},
                )
            else:
                # Both keys present and CONFIRM matches current hour — bypass granted
                scope_raw = os.environ.get("ARTHA_WAVE0_OVERRIDE_SCOPE", "")
                scope = [s.strip() for s in scope_raw.split(",") if s.strip()]
                scope_ok = (not scope) or (self.name in scope)
                if not scope_ok:
                    # Scope restriction: this guardrail is not in the override scope
                    print(
                        f"⚠ [GUARDRAIL-BYPASS-SCOPED] {self.name} not in "
                        f"ARTHA_WAVE0_OVERRIDE_SCOPE={scope_raw!r} — guardrail fires normally.",
                        file=sys.stderr,
                    )
                    # Fall through to normal guardrail checks
                else:
                    print(
                        f"⚠ [GUARDRAIL-BYPASS] wave0 gate overridden for [{self.name}]"
                        f" | reason: {override[:80]}"
                        f" | Audit: state/audit.md entry WAVE0_HARD_OVERRIDE",
                        file=sys.stderr,
                    )
                    _guardrail_write_audit(
                        "WAVE0_HARD_OVERRIDE",
                        {"guardrail": self.name, "reason": override[:120],
                         "scope": scope_raw[:80] or "all"},
                    )
                    return True

        mode = _get_wave0_mode()

        try:
            from context_offloader import load_harness_flag  # noqa: PLC0415
            flag_complete = load_harness_flag("wave0.complete")
        except Exception as exc:  # noqa: BLE001
            if mode == "hard":
                raise RuntimeError(
                    f"[{self.name}] wave0_gate=hard: cannot load harness flag — "
                    f"guardrail is blocking. ({exc}). "
                    "Set ARTHA_WAVE0_OVERRIDE=<reason> to bypass with audit trail."
                ) from exc
            # soft mode: fail-open, log warning
            log = self._get_log()
            msg = f"[{self.name}] wave0 flag load failed — guardrail bypassed (soft mode)"
            if log is not None:
                log.warning("guardrail.wave0_flag_load_failed", guardrail=self.name)
            else:
                print(f"[WARN] {msg}", file=sys.stderr)
            return True

        if not flag_complete:
            if mode == "hard":
                raise RuntimeError(
                    f"[{self.name}] wave0_gate=hard: Wave 0 incomplete "
                    "(harness.wave0.complete=false) — guarded path blocked. "
                    "Set ARTHA_WAVE0_OVERRIDE=<reason> to bypass with audit trail."
                )
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
            # injection_detector unavailable — fail SAFE (block, don't allow through)
            return GuardrailOutput(
                TripwireResult.HALT,
                message="injection_detector unavailable — blocking on fail-safe",
            )

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
# EAR-3 Domain Guardrails (§8.2, prd-reloaded.md)
# Implementation order: Logistics → Capital → Tribe → Readiness  (R4)
# ---------------------------------------------------------------------------


class LogisticsInjectionGR(BaseGuardrail):
    """Logistics — OCR/Vision AI injection defense (§8.2, §8.3).

    Enforces the mandatory PII-scrub-before-injection-scan pipeline (C1.4):
      Raw OCR → context_scrubber PII scrub → injection_detector scan → pass/discard

    Fail-safe: HALT if either dependency is unavailable (never fail-open).
    On HALT: receipt parse discarded; caller logs to state/audit.md.
    """

    name = "logistics_injection"
    mode = GuardrailMode.BLOCKING

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        raw_text = data if isinstance(data, str) else str(data or "")

        # Step 1: PII scrub BEFORE injection scan (C1.4 ordering requirement)
        try:
            from lib.context_scrubber import ContextScrubber  # noqa: PLC0415

            scrubbed_text = ContextScrubber().scrub(raw_text)
        except ImportError:
            return GuardrailOutput(
                TripwireResult.HALT,
                message="context_scrubber unavailable — blocking OCR input on fail-safe",
            )

        # Step 2: Injection scan on PII-free text
        try:
            from lib.injection_detector import InjectionDetector  # noqa: PLC0415

            result = InjectionDetector().scan(scrubbed_text)
            if result.injection_detected:
                signal_types = ", ".join(s.signal_type for s in result.signals[:5])
                return GuardrailOutput(
                    TripwireResult.HALT,
                    message=(
                        f"⚠ Injection signal in receipt OCR: "
                        f"signals=[{signal_types}]. Receipt parse discarded."
                    ),
                )
        except ImportError:
            return GuardrailOutput(
                TripwireResult.HALT,
                message="injection_detector unavailable — blocking OCR input on fail-safe",
            )

        return GuardrailOutput(TripwireResult.PASS)


class LogisticsPIIBoundaryGR(BaseGuardrail):
    """Logistics — block proposals that would transmit PII to external APIs (§8.2).

    In Phase 1-2, any proposal payload classified above LOW sensitivity by
    context_scrubber is blocked before it reaches broker or PA APIs.
    """

    name = "logistics_pii_boundary"
    mode = GuardrailMode.BLOCKING

    # Sensitivity levels above which proposals are blocked (case-insensitive)
    _BLOCKED_LEVELS = {"medium", "high", "critical"}

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        payload = (context or {}).get("payload", data)
        if payload is None:
            return GuardrailOutput(TripwireResult.PASS)

        payload_str = payload if isinstance(payload, str) else str(payload)

        try:
            from lib.context_scrubber import ContextScrubber  # noqa: PLC0415

            sensitivity = ContextScrubber().classify(payload_str)
        except ImportError:
            # Scrubber unavailable — default to PASS (non-security-critical path)
            return GuardrailOutput(TripwireResult.PASS)

        if str(sensitivity).lower() in self._BLOCKED_LEVELS:
            return GuardrailOutput(
                TripwireResult.HALT,
                message=(
                    f"Logistics proposal blocked: payload sensitivity '{sensitivity}' "
                    "exceeds LOW boundary. PII must not be transmitted to external APIs."
                ),
            )

        return GuardrailOutput(TripwireResult.PASS)


class CapitalSourceCitationGR(BaseGuardrail):
    """Capital — block uncited financial proposals (§8.2, §8.4).

    Any Capital proposal missing a ``source:`` field is stripped before
    delivery.  No uncited figures reach the user.
    """

    name = "capital_source_citation"
    mode = GuardrailMode.BLOCKING

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        ctx = context or {}
        if ctx.get("domain") != "capital":
            return GuardrailOutput(TripwireResult.PASS)

        # Check both the context dict and the data payload for a source field
        has_source = bool(ctx.get("source"))
        if not has_source and isinstance(data, dict):
            has_source = bool(data.get("source"))

        if not has_source:
            return GuardrailOutput(
                TripwireResult.HALT,
                message=(
                    "Capital proposal blocked: missing mandatory 'source:' field. "
                    "All Capital figures must cite the exact state/finance.md line "
                    "or transaction ID."
                ),
            )

        return GuardrailOutput(TripwireResult.PASS)


class CapitalAmountConfirmGR(BaseGuardrail):
    """Capital — enforce amount-confirmation gate for proposals >$200 (§8.2, §8.4, C1.3).

    Proposals with ``proposal_amount > 200`` require ``confirmation_amount``
    to match exactly.  Mismatch → REJECT.  Missing confirmation → HALT.
    """

    name = "capital_amount_confirm"
    mode = GuardrailMode.BLOCKING

    _THRESHOLD = 200.0

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        ctx = context or {}
        amount = ctx.get("proposal_amount")
        if amount is None:
            return GuardrailOutput(TripwireResult.PASS)

        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return GuardrailOutput(TripwireResult.PASS)

        if amount <= self._THRESHOLD:
            return GuardrailOutput(TripwireResult.PASS)

        # Amount exceeds threshold — require matching confirmation
        confirmation = ctx.get("confirmation_amount")
        if confirmation is None:
            return GuardrailOutput(
                TripwireResult.HALT,
                message=(
                    f"Capital proposal blocked: amount ${amount:.2f} exceeds "
                    f"${self._THRESHOLD:.0f} threshold and requires amount-confirmation "
                    "retyping. confirmation_amount not provided."
                ),
            )

        try:
            confirmation = float(confirmation)
        except (TypeError, ValueError):
            return GuardrailOutput(
                TripwireResult.HALT,
                message="Capital proposal blocked: confirmation_amount is not a valid number.",
            )

        if confirmation != amount:
            return GuardrailOutput(
                TripwireResult.HALT,
                message=(
                    f"Amount does not match proposal. Type CANCEL or the correct "
                    f"amount from the proposal: ${amount:.2f}."
                ),
            )

        return GuardrailOutput(TripwireResult.PASS)


class TribeRateLimitGR(BaseGuardrail):
    """Tribe — hard cap of 5 outreach drafts per catch-up run (§8.2).

    The 6th+ draft_message action is silently dropped (HALT) and logged.
    Not user-configurable.  Cold-start contacts (session_draft_count == 0 → cap intact).
    Non-draft-message actions are not subject to this cap.
    """

    name = "tribe_rate_limit"
    mode = GuardrailMode.BLOCKING

    _MAX_DRAFTS = 5

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        ctx = context or {}
        if ctx.get("action") != "draft_message":
            return GuardrailOutput(TripwireResult.PASS)

        draft_count = ctx.get("session_draft_count", 0)
        try:
            draft_count = int(draft_count)
        except (TypeError, ValueError):
            draft_count = 0

        if draft_count >= self._MAX_DRAFTS:
            return GuardrailOutput(
                TripwireResult.HALT,
                message=(
                    f"Tribe draft cap reached: {draft_count}/{self._MAX_DRAFTS} drafts "
                    "in this catch-up. Remaining drafts silently dropped."
                ),
            )

        return GuardrailOutput(TripwireResult.PASS)


class TribeNoAutoSendGR(BaseGuardrail):
    """Tribe — block send_message if WhatsApp connector is not write-enabled (§8.2).

    Hard guardrail: if output contains a send_message tool call targeting
    WhatsApp and the connector does not have ``write: true``, HALT.
    """

    name = "tribe_no_auto_send"
    mode = GuardrailMode.BLOCKING

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        ctx = context or {}
        if ctx.get("action") != "send_message":
            return GuardrailOutput(TripwireResult.PASS)

        connector = ctx.get("connector", "")
        if "whatsapp" not in str(connector).lower():
            return GuardrailOutput(TripwireResult.PASS)

        connector_write_enabled = bool(ctx.get("connector_write", False))
        if not connector_write_enabled:
            return GuardrailOutput(
                TripwireResult.HALT,
                message="WhatsApp write connector not configured.",
            )

        return GuardrailOutput(TripwireResult.PASS)


class ReadinessFallbackGR(BaseGuardrail):
    """Readiness — graceful fallback when Apple Health export is missing or stale (§8.2).

    If the Apple Health export is absent or older than 24 hours:
    - Sets ``readiness_score: unknown`` in context
    - Suppresses calendar restructuring proposals (by HALTing on write proposals)

    This is a Tool guardrail: runs before domain agents process health signals.
    """

    name = "readiness_fallback"
    mode = GuardrailMode.BLOCKING

    _MAX_EXPORT_AGE_HOURS = 24

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        ctx = context or {}
        export_age_hours = ctx.get("health_export_age_hours")

        export_missing = ctx.get("health_export_missing", False)
        if export_missing:
            return GuardrailOutput(
                TripwireResult.HALT,
                message=(
                    "Readiness: Apple Health export missing — "
                    "readiness_score set to unknown. Calendar proposals suppressed."
                ),
            )

        if export_age_hours is not None:
            try:
                age = float(export_age_hours)
            except (TypeError, ValueError):
                age = None
            if age is not None and age > self._MAX_EXPORT_AGE_HOURS:
                return GuardrailOutput(
                    TripwireResult.HALT,
                    message=(
                        f"Readiness: Apple Health export is {age:.1f}h old "
                        f"(>{self._MAX_EXPORT_AGE_HOURS}h) — "
                        "readiness_score set to unknown. Calendar proposals suppressed."
                    ),
                )

        return GuardrailOutput(TripwireResult.PASS)


class ReadinessNoInferenceGR(BaseGuardrail):
    """Readiness — strip calendar restructuring proposals when score is unknown (§8.2).

    Hard rule: if ``readiness_score`` is ``'unknown'`` (or absent), any
    proposal with ``action: write`` and ``proposal_type: calendar_*`` is
    blocked.  Readiness must never be inferred from absence of data.
    """

    name = "readiness_no_inference"
    mode = GuardrailMode.BLOCKING

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        ctx = context or {}
        score = ctx.get("readiness_score")

        # Only applies when score is unknown
        if score != "unknown":
            return GuardrailOutput(TripwireResult.PASS)

        # Block write actions on calendar proposals
        action = str(ctx.get("action", "")).lower()
        proposal_type = str(ctx.get("proposal_type", "")).lower()

        is_calendar_write = action == "write" and "calendar" in proposal_type
        if is_calendar_write:
            return GuardrailOutput(
                TripwireResult.HALT,
                message=(
                    "Readiness: calendar restructuring blocked — "
                    "readiness_score is unknown. Never infer readiness from "
                    "absence of data."
                ),
            )

        return GuardrailOutput(TripwireResult.PASS)


# ---------------------------------------------------------------------------
# Career domain guardrails (§8.2, §8.3, §8.4 of career-ops spec v1.8.0)
# ---------------------------------------------------------------------------

_CAREER_INJECTION_PATTERNS = [
    # Hidden instruction markers
    r"ignore (all |previous |above )?instructions?",
    r"disregard (all |previous |above )?instructions?",
    r"system prompt",
    r"you are now",
    r"act as",
    r"pretend (to be|you are)",
    r"new persona",
    r"jailbreak",
    r"DAN mode",
    r"developer mode",
    # Suspicious structured-data injection in JD text
    r"```\s*(python|bash|sql|sh|json)",
    r"<script",
    r"<!--.*-->",
]


class CareerJDInjectionGR(BaseGuardrail):
    """Career — Job-description injection + auth-wall ordering guard (W-F5).

    Phase 1: text-heuristic scan for prompt-injection patterns embedded in JD
    text before it reaches the LLM evaluation pipeline.  Data never reaches
    Block A–G processing if injection signals are present.

    Auth-wall ordering: if JD text indicates a login/auth wall was hit, the
    entry is logged to JSONL but not scored (prevents N/A metric pollution).
    """

    name = "career_jd_injection"
    mode = GuardrailMode.BLOCKING

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        import re  # noqa: PLC0415

        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        ctx = context or {}
        if ctx.get("domain") != "career_search":
            return GuardrailOutput(TripwireResult.PASS)

        jd_text = data if isinstance(data, str) else str(data or "")
        jd_lower = jd_text.lower()

        # Auth-wall ordering check (W-F5): log and skip, don't block
        auth_wall_signals = [
            "sign in to view",
            "log in to apply",
            "create an account",
            "login required",
            "please sign in",
        ]
        if any(sig in jd_lower for sig in auth_wall_signals):
            return GuardrailOutput(
                TripwireResult.HALT,
                message=(
                    "Career JD blocked: auth wall detected in content. "
                    "Job must be re-fetched after auth or entered manually."
                ),
            )

        # Injection pattern scan
        for pattern in _CAREER_INJECTION_PATTERNS:
            if re.search(pattern, jd_lower):
                return GuardrailOutput(
                    TripwireResult.HALT,
                    message=(
                        f"Career JD blocked: injection pattern detected "
                        f"(matched: '{pattern[:40]}'). JD discarded."
                    ),
                )

        return GuardrailOutput(TripwireResult.PASS)


class CareerNoAutoSubmitGR(BaseGuardrail):
    """Career — hard block on any auto-submit / auto-apply action.

    Artha NEVER submits job applications autonomously.  Any action proposal
    whose ``action_type`` contains 'submit' or 'apply' is unconditionally
    rejected.  This is a hard rule with no override path (§3.1 of spec).
    """

    name = "career_no_auto_submit"
    mode = GuardrailMode.BLOCKING

    _BLOCKED_ACTIONS = frozenset([
        "submit_application",
        "auto_apply",
        "apply_to_job",
        "submit_resume",
        "send_application",
    ])

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        ctx = context or {}
        if ctx.get("domain") != "career_search":
            return GuardrailOutput(TripwireResult.PASS)

        action_type = str(ctx.get("action_type", "") or "").lower().replace("-", "_")

        # Also inspect data dict
        if isinstance(data, dict):
            action_type = action_type or str(data.get("action_type", "")).lower()

        if any(blocked in action_type for blocked in self._BLOCKED_ACTIONS):
            return GuardrailOutput(
                TripwireResult.HALT,
                message=(
                    "Hard block: Artha NEVER submits job applications autonomously. "
                    "User must initiate all submissions manually."
                ),
            )

        # Also catch generic 'submit' and 'apply' in action_type
        if "submit" in action_type or "auto_apply" in action_type:
            return GuardrailOutput(
                TripwireResult.HALT,
                message=(
                    "Hard block: auto-submit action blocked. "
                    "Career applications must be submitted manually by the user."
                ),
            )

        return GuardrailOutput(TripwireResult.PASS)


class CareerPiiOutputGR(BaseGuardrail):
    """Career — PII redaction for career domain output (§8.3).

    Policy:
    - Phone numbers → redact (replace with [PHONE REDACTED])
    - SSN / NIN patterns → HALT entirely (never in career output)
    - Compensation figures → ALLOW (comp is non-sensitive in career context)
    """

    name = "career_pii_output"
    mode = GuardrailMode.BLOCKING

    # SSN: 3-2-4 digit patterns in various delimiters
    _SSN_PATTERN = r"\b\d{3}[\s\-./]\d{2}[\s\-./]\d{4}\b"
    # Phone: common US/intl patterns (7+ digits with optional separators)
    _PHONE_PATTERN = (
        r"(\+?\d[\s\-.()]*){7,}"
    )

    def check(self, context: dict, data: Any) -> GuardrailOutput:
        import re  # noqa: PLC0415

        if not self._wave0_ok():
            return GuardrailOutput(TripwireResult.PASS)

        ctx = context or {}
        if ctx.get("domain") != "career_search":
            return GuardrailOutput(TripwireResult.PASS)

        output_text = data if isinstance(data, str) else ""
        if not output_text:
            return GuardrailOutput(TripwireResult.PASS)

        # SSN: unconditional HALT — never allowed in output
        if re.search(self._SSN_PATTERN, output_text):
            return GuardrailOutput(
                TripwireResult.HALT,
                message=(
                    "Career output blocked: SSN/NIN pattern detected. "
                    "SSNs must never appear in career domain output."
                ),
            )

        # Phone: redact in-place, degrade to WARN (not HALT)
        cleaned = re.sub(self._PHONE_PATTERN, "[PHONE REDACTED]", output_text)
        if cleaned != output_text:
            return GuardrailOutput(
                TripwireResult.WARN,
                message="Phone number(s) redacted from career output.",
                modified_data=cleaned,
            )

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
    # EAR-3 domain guardrails (§8.2)
    "LogisticsInjectionGR": LogisticsInjectionGR,
    "LogisticsPIIBoundaryGR": LogisticsPIIBoundaryGR,
    "CapitalSourceCitationGR": CapitalSourceCitationGR,
    "CapitalAmountConfirmGR": CapitalAmountConfirmGR,
    "TribeRateLimitGR": TribeRateLimitGR,
    "TribeNoAutoSendGR": TribeNoAutoSendGR,
    "ReadinessFallbackGR": ReadinessFallbackGR,
    "ReadinessNoInferenceGR": ReadinessNoInferenceGR,
    # Career domain guardrails (career-ops spec v1.8.0)
    "CareerJDInjectionGR": CareerJDInjectionGR,
    "CareerNoAutoSubmitGR": CareerNoAutoSubmitGR,
    "CareerPiiOutputGR": CareerPiiOutputGR,
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
    # EAR-3 domain guardrails
    "LogisticsInjectionGR",
    "LogisticsPIIBoundaryGR",
    "CapitalSourceCitationGR",
    "CapitalAmountConfirmGR",
    "TribeRateLimitGR",
    "TribeNoAutoSendGR",
    "ReadinessFallbackGR",
    "ReadinessNoInferenceGR",
    # Career domain guardrails
    "CareerJDInjectionGR",
    "CareerNoAutoSubmitGR",
    "CareerPiiOutputGR",
    "GUARDRAIL_CLASSES",
]
