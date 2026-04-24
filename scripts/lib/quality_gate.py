"""
quality_gate.py — S-01 Quality Gates between pipeline phases.
specs/steal.md §15.4.2

A quality gate is a named checkpoint between pipeline stages (post_fetch,
post_process, post_reason).  Each gate runs a set of check functions; on
failure it retries up to max_retries times before hard-failing.

On hard-fail, a `pipeline.gate_failed` telemetry event is emitted
(best-effort — if telemetry is unavailable the gate still returns a result).

Design constraints:
  - R8: dataclasses only, no Pydantic
  - Checks are plain callables (() -> bool); no external dependencies required
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    """Result of running a quality gate (one attempt or all retries)."""
    gate_name: str
    passed: bool
    checks: list[dict]   # list of {name, passed, message}
    attempt: int         # which attempt succeeded (or total attempts on fail)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_gate(
    gate_name: str,
    checks: list[Callable[[], bool]],
    max_retries: int = 2,
) -> GateResult:
    """Run *checks* for *gate_name*, retrying up to *max_retries* times on failure.

    Args:
        gate_name:   Human-readable gate identifier (e.g. "post_fetch").
        checks:      List of callables that return bool (True = passed).
        max_retries: Total retry count after the first attempt (default 2
                     means up to 3 total attempts).

    Returns:
        GateResult.passed=True on the first attempt where all checks pass.
        GateResult.passed=False if all attempts are exhausted.

    Side effects:
        On hard-fail (all retries exhausted), emits a ``pipeline.gate_failed``
        telemetry event via scripts/lib/telemetry (best-effort; failures here
        are silently swallowed).
    """
    total_attempts = max_retries + 1

    for attempt in range(1, total_attempts + 1):
        check_results: list[dict] = []
        all_passed = True

        for check_fn in checks:
            name = getattr(check_fn, "__name__", repr(check_fn))
            try:
                result = bool(check_fn())
            except Exception as exc:
                result = False
                msg = f"exception: {exc}"
            else:
                msg = "passed" if result else "failed"

            check_results.append({"name": name, "passed": result, "message": msg})
            if not result:
                all_passed = False

        if all_passed:
            return GateResult(gate_name, True, check_results, attempt)

        if attempt >= total_attempts:
            # Hard fail — emit telemetry best-effort
            _emit_gate_failed(gate_name, check_results, attempt)
            return GateResult(gate_name, False, check_results, attempt)

    # Unreachable, but satisfies type checker
    return GateResult(gate_name, False, [], total_attempts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _emit_gate_failed(
    gate_name: str, check_results: list[dict], attempt: int
) -> None:
    """Best-effort telemetry emission on hard gate failure."""
    try:
        import sys as _sys
        from pathlib import Path as _Path

        _lib = str(_Path(__file__).resolve().parent)
        if _lib not in _sys.path:
            _sys.path.insert(0, _lib)

        import telemetry as _tel  # type: ignore[import-not-found]

        _tel.emit(
            "pipeline.gate_failed",
            gate=gate_name,
            attempt=attempt,
            failed_checks=[c["name"] for c in check_results if not c["passed"]],
        )
    except Exception:
        pass  # Never let telemetry errors propagate
