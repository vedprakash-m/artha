"""tests/unit/test_artha_context_eval.py — Eval-specific tests for artha_context.py.

Tests _estimate_pressure() logic.
Ref: specs/eval.md EV-0b, T-EV-0b-01 to T-EV-0b-02
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def ctx():
    return _load_module("artha_context_ev0b", _SCRIPTS_DIR / "artha_context.py")


# ===========================================================================
# T-EV-0b-01: _estimate_pressure(100_000) → YELLOW  (50% of 200K window)
# ===========================================================================

def test_estimate_pressure_yellow_at_50_percent(ctx):
    """T-EV-0b-01: 100K tokens (50% of 200K) must map to YELLOW pressure tier."""
    result = ctx._estimate_pressure(100_000)
    assert result == ctx.ContextPressure.YELLOW, (
        f"Expected YELLOW at 50% context, got: {result}"
    )


# ===========================================================================
# T-EV-0b-02: _estimate_pressure(None) → GREEN  (safe default)
# ===========================================================================

def test_estimate_pressure_green_when_none(ctx):
    """T-EV-0b-02: None token count must default to GREEN pressure tier."""
    result = ctx._estimate_pressure(None)
    assert result == ctx.ContextPressure.GREEN, (
        f"Expected GREEN for None input, got: {result}"
    )
