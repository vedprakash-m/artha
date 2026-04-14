#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/middleware/guardrail_registry.py — Guardrail chain loader and runner.

Loads the active guardrail configuration from ``config/guardrails.yaml`` and
composes three typed chains:

  - ``run_input_guardrails(context, data)``  — before step data is processed
  - ``run_tool_guardrails(context, data)``   — before connector invocations
  - ``run_output_guardrails(context, data)`` — on step output before delivery

Each chain runs its guardrails left-to-right.  In BLOCKING mode, the first
HALT or FALLBACK result short-circuits the chain and raises the appropriate
exception.  In PARALLEL mode, all guardrails run regardless of result.

Typical usage (in pipeline.py or action_executor.py):

    from middleware.guardrail_registry import GuardrailRegistry

    registry = GuardrailRegistry()
    registry.run_input_guardrails(context, user_text)
    result = run_connector(...)
    registry.run_output_guardrails(context, result)

Ref: specs/agent-fw.md §3.1 (AFW-1)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from middleware.guardrails import (
    GUARDRAIL_CLASSES,
    BaseGuardrail,
    GuardrailMode,
    GuardrailOutput,
    GuardrailViolation,
    TripwireResult,
)

# Default config location — relative to Artha root
_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "guardrails.yaml"

# DEBT-004: Module-level degraded flag.
# Set to True when guardrails.yaml fails to load.  Consumers (briefing, pipeline)
# check this flag to surface a SAFETY banner.  Reset between test cases by setting
# guardrail_registry._GUARDRAILS_DEGRADED = False in setUp/tearDown.
_GUARDRAILS_DEGRADED: bool = False


def _load_yaml(path: Path) -> dict:
    """Load a YAML file with minimal dependencies.

    DEBT-004: Retry-once logic guards against transient OneDrive file locks.
    On any failure, sets _GUARDRAILS_DEGRADED = True and returns {} so callers
    can detect the degraded state.
    """
    global _GUARDRAILS_DEGRADED  # noqa: PLW0603

    def _attempt(p: Path) -> dict | None:
        try:
            import yaml  # noqa: PLC0415
            with open(p, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except ImportError:
            pass  # yaml not available — fall through to config_loader
        except Exception:  # noqa: BLE001  # FileNotFoundError, yaml.ParserError, etc.
            return None
        try:
            from lib.config_loader import load_config  # noqa: PLC0415
            return load_config(p.stem) or {}
        except Exception:  # noqa: BLE001
            return None

    result = _attempt(path)
    if result is None:
        # Retry once after 1s (OneDrive transient lock mitigation)
        time.sleep(1)
        result = _attempt(path)

    if result is None:
        _GUARDRAILS_DEGRADED = True
        return {}

    return result


def _build_guardrail(spec: dict) -> BaseGuardrail | None:
    """Instantiate a guardrail from a config spec dict."""
    class_name = spec.get("class", "")
    cls = GUARDRAIL_CLASSES.get(class_name)
    if cls is None:
        print(
            f"[guardrail_registry] Unknown guardrail class {class_name!r} — skipped.",
            file=sys.stderr,
        )
        return None

    cfg = spec.get("config") or {}
    try:
        if cfg:
            return cls(**cfg)  # type: ignore[call-arg]
        return cls()
    except TypeError as exc:
        print(
            f"[guardrail_registry] Failed to instantiate {class_name}: {exc}",
            file=sys.stderr,
        )
        return None


class GuardrailRegistry:
    """Loads and runs typed guardrail chains.

    Parameters
    ----------
    config_path:
        Optional override for the YAML config location.  Defaults to
        ``config/guardrails.yaml`` relative to the Artha root.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        global _GUARDRAILS_DEGRADED  # noqa: PLW0603
        cfg_path = config_path or _DEFAULT_CONFIG
        config = _load_yaml(cfg_path)

        self._input_chain: list[tuple[BaseGuardrail, GuardrailMode]] = []
        self._tool_chain: list[tuple[BaseGuardrail, GuardrailMode]] = []
        self._output_chain: list[tuple[BaseGuardrail, GuardrailMode]] = []

        for section, chain in (
            ("input", self._input_chain),
            ("tool", self._tool_chain),
            ("output", self._output_chain),
        ):
            for spec in config.get(section, []):
                if not spec.get("enabled", True):
                    continue
                guardrail = _build_guardrail(spec)
                if guardrail is None:
                    continue
                mode_str = spec.get("mode", "blocking").lower()
                mode = (
                    GuardrailMode.PARALLEL
                    if mode_str == "parallel"
                    else GuardrailMode.BLOCKING
                )
                chain.append((guardrail, mode))

        # DEBT-004: Detect and surface guardrail degradation.
        # Empty chains + degraded flag = guardrails.yaml failed to load.
        # This is architecturally a hard failure: no PII scan, no injection
        # detection, no rate limiting. Emit CRITICAL to stderr + audit log.
        all_chains_empty = (
            not self._input_chain
            and not self._tool_chain
            and not self._output_chain
        )
        if _GUARDRAILS_DEGRADED or (all_chains_empty and not config):
            _GUARDRAILS_DEGRADED = True
            print(
                "\nCRITICAL: Guardrails failed to load from "
                f"{cfg_path}\n"
                "  All safety chains (PII scan, injection detection, rate limiting) are INACTIVE.\n"
                "  This session is UNGUARDED. Fix guardrails.yaml before proceeding.\n"
                "  Override (experts only): python scripts/preflight.py --force-no-guardrails\n",
                file=sys.stderr,
            )
            self._emit_guardrail_degraded_audit(cfg_path)

    def _emit_guardrail_degraded_audit(self, cfg_path: Path) -> None:
        """Write GUARDRAIL_DEGRADED to state/audit.md (best-effort, non-fatal)."""
        try:
            from datetime import datetime, timezone  # noqa: PLC0415
            artha_dir = Path(__file__).resolve().parents[2]
            audit_log = artha_dir / "state" / "audit.md"
            if audit_log.exists():
                ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
                entry = (
                    f"[{ts}] GUARDRAIL_DEGRADED"
                    f" | config: {cfg_path}"
                    f" | chains: input={len(self._input_chain)}"
                    f" tool={len(self._tool_chain)}"
                    f" output={len(self._output_chain)}\n"
                )
                with open(audit_log, "a", encoding="utf-8") as f:
                    f.write(entry)
        except Exception:  # noqa: BLE001
            pass  # audit write is best-effort; stderr alert is the primary signal

    # ------------------------------------------------------------------
    # Public runners
    # ------------------------------------------------------------------

    def run_input_guardrails(self, context: dict, data: Any) -> GuardrailOutput:
        """Run all input guardrails.

        In BLOCKING mode, the first non-PASS result halts the chain.

        Returns the terminal GuardrailOutput (PASS if all passed).
        Raises GuardrailViolation if a guardrail raises it.
        """
        return self._run_chain(self._input_chain, context, data)

    def run_tool_guardrails(self, context: dict, data: Any) -> GuardrailOutput:
        """Run all tool guardrails before connector invocations."""
        return self._run_chain(self._tool_chain, context, data)

    def run_output_guardrails(self, context: dict, data: Any) -> GuardrailOutput:
        """Run all output guardrails on step results.

        May raise ``GuardrailViolation`` for unredactable PII leaks.
        """
        return self._run_chain(self._output_chain, context, data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_chain(
        self,
        chain: list[tuple[BaseGuardrail, GuardrailMode]],
        context: dict,
        data: Any,
    ) -> GuardrailOutput:
        last: GuardrailOutput = GuardrailOutput(TripwireResult.PASS)

        for guardrail, mode in chain:
            try:
                output = guardrail.check(context, data)
            except GuardrailViolation:
                raise  # never swallow — propagate to caller
            except Exception as exc:  # noqa: BLE001
                # Guardrail errors must not crash the pipeline — log and continue
                log = guardrail._get_log()
                if log is not None:
                    log.error(
                        "guardrail.unexpected_error",
                        guardrail=guardrail.name,
                        error=str(exc),
                    )
                else:
                    print(
                        f"[WARN] Guardrail {guardrail.name!r} raised {exc}",
                        file=sys.stderr,
                    )
                continue

            last = output

            # BLOCKING mode: halt chain on non-PASS result
            if mode == GuardrailMode.BLOCKING and output.result != TripwireResult.PASS:
                return output

        return last


__all__ = ["GuardrailRegistry"]
