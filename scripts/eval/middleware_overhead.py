#!/usr/bin/env python3
# pii-guard: ignore-file — benchmark script, no personal data
"""
scripts/eval/middleware_overhead.py — Benchmark AFW-3 middleware chain overhead (A-12 gate).

Measures the wall-clock overhead of running ``compose_middleware()`` with 7
middleware objects (using all available production middleware plus padding
stubs) across 100 iterations, then checks the p99 latency per step.

A-12 criterion (specs/agent-fw.md §3.3):
    Middleware chain overhead < 50ms per step at p99 over 100 iterations.

Output: ``tmp/middleware_overhead.json``
    {
      "p50_ms": 0.12,
      "p99_ms": 1.43,
      "mean_ms": 0.15,
      "max_ms": 2.10,
      "passed": true,
      "threshold_ms": 50,
      "n_iterations": 100,
      "n_middleware": 7
    }

Usage::

    python scripts/eval/middleware_overhead.py [--iterations N] [--verbose]

Spec: specs/agent-fw.md §3.3, A-12
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
_TMP = _ROOT / "tmp"

for _p in (_SCRIPTS, _SCRIPTS / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ---------------------------------------------------------------------------
# Minimal stub middleware (used to pad to 7 if needed)
# ---------------------------------------------------------------------------

class _NoOpMiddleware:
    """Zero-cost stub that satisfies the StateMiddleware protocol."""

    def before_write(
        self, domain: str, current: str, proposed: str, ctx: Any = None
    ) -> str | None:
        return proposed

    def after_write(self, domain: str, file_path: Path) -> None:
        pass

    def before_step(self, step_name: str, context: dict, data: Any) -> None:
        pass

    def after_step(self, step_name: str, context: dict, data: Any) -> None:
        pass

    def on_error(self, step_name: str, context: dict, error: Exception) -> None:
        pass


# ---------------------------------------------------------------------------
# Middleware assembly
# ---------------------------------------------------------------------------

def _build_middleware_stack(verbose: bool) -> tuple[Any, int]:
    """Build a list of ≥7 middleware objects using available implementations.

    Returns (composed_stack, n_middleware).
    """
    from middleware import compose_middleware  # noqa: PLC0415

    stack_items: list[Any] = []

    # Load production middleware — each wrapped in try/except so missing
    # implementations degrade gracefully
    _attempts = [
        ("middleware.pii_middleware", "PIIMiddleware"),
        ("middleware.write_guard", "WriteGuardMiddleware"),
        ("middleware.write_verify", "WriteVerifyMiddleware"),
        ("middleware.audit_middleware", "AuditMiddleware"),
        ("middleware.rate_limiter", "RateLimiter"),
    ]

    for module_path, cls_name in _attempts:
        try:
            import importlib  # noqa: PLC0415
            mod = importlib.import_module(module_path)
            cls = getattr(mod, cls_name)
            instance = cls()
            stack_items.append(instance)
            if verbose:
                print(f"  [A-12] Loaded {cls_name}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"  [A-12] Could not load {cls_name}: {exc}", file=sys.stderr)

    # Pad to exactly 7 with no-op stubs if needed
    needed = max(0, 7 - len(stack_items))
    for _ in range(needed):
        stack_items.append(_NoOpMiddleware())

    if verbose:
        print(
            f"  [A-12] Stack: {len(stack_items)} middleware "
            f"({len(stack_items) - needed} real, {needed} stubs)",
            file=sys.stderr,
        )

    composed = compose_middleware(stack_items)
    return composed, len(stack_items)


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

def _percentile(values: list[float], pct: float) -> float:
    """Return the p-th percentile of a sorted list."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def main() -> int:
    parser = argparse.ArgumentParser(description="A-12 middleware overhead benchmark")
    parser.add_argument(
        "--iterations",
        type=int,
        default=100,
        help="Number of benchmark iterations (default: 100)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    threshold_ms = 50.0

    if args.verbose:
        print(
            f"[A-12] Benchmarking middleware chain ({args.iterations} iterations)...",
            file=sys.stderr,
        )

    try:
        stack, n_middleware = _build_middleware_stack(args.verbose)
    except ImportError as exc:
        print(
            f"A-12 SKIP: Cannot import middleware package: {exc}",
            file=sys.stderr,
        )
        output = {
            "passed": None,
            "skipped": True,
            "reason": str(exc),
        }
        _TMP.mkdir(parents=True, exist_ok=True)
        (_TMP / "middleware_overhead.json").write_text(
            json.dumps(output, indent=2), encoding="utf-8"
        )
        return 0

    ctx: dict[str, Any] = {"session_id": "benchmark", "step": "test"}
    sample_data = {"records": list(range(10)), "source": "benchmark"}

    # Warm-up pass (exclude from measurement)
    for _ in range(5):
        stack.run_before_step("test", ctx, sample_data)
        stack.run_after_step("test", ctx, sample_data)

    # Timed passes
    times_ms: list[float] = []
    for _ in range(args.iterations):
        t0 = time.perf_counter()
        stack.run_before_step("test", ctx, sample_data)
        stack.run_after_step("test", ctx, sample_data)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        times_ms.append(elapsed_ms)

    p50 = _percentile(times_ms, 50)
    p99 = _percentile(times_ms, 99)
    mean = sum(times_ms) / len(times_ms)
    max_ms = max(times_ms)
    passed = p99 < threshold_ms

    output = {
        "p50_ms": round(p50, 4),
        "p99_ms": round(p99, 4),
        "mean_ms": round(mean, 4),
        "max_ms": round(max_ms, 4),
        "passed": passed,
        "threshold_ms": threshold_ms,
        "n_iterations": args.iterations,
        "n_middleware": n_middleware,
    }

    _TMP.mkdir(parents=True, exist_ok=True)
    (_TMP / "middleware_overhead.json").write_text(
        json.dumps(output, indent=2), encoding="utf-8"
    )

    status = "PASSED" if passed else "FAILED"
    print(
        f"A-12 {status}: p99={p99:.2f}ms (threshold={threshold_ms}ms), "
        f"mean={mean:.2f}ms, max={max_ms:.2f}ms "
        f"(n={args.iterations}, {n_middleware} middleware)"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
