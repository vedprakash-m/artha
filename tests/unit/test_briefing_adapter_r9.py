"""tests/unit/test_briefing_adapter_r9.py — Unit tests for EV-13 R9 quality regression rule.

Tests _r9_quality_regression() in scripts/briefing_adapter.py.
All data is synthetic (DD-5).
Ref: specs/eval.md EV-13, T-EV-13-01 to T-EV-13-04
"""
from __future__ import annotations

import importlib.util
import sys
import types
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
def adapter():
    return _load_module("briefing_adapter_r9", _SCRIPTS_DIR / "briefing_adapter.py")


# ---------------------------------------------------------------------------
# Helper: fake MetricStore
# ---------------------------------------------------------------------------

def _make_fake_metric_store(trend: str, run_count: int, avg_quality: float):
    """Return a fake MetricStore class whose get_quality_trend returns the given values."""

    class FakeMetricStore:
        def __init__(self, _root_dir):
            pass

        def get_quality_trend(self, window: int = 7):
            return {
                "trend": trend,
                "run_count": run_count,
                "avg_quality": avg_quality,
            }

    return FakeMetricStore


# ===========================================================================
# T-EV-13-01: regressing trend + ≥7 runs → R9 string returned
# ===========================================================================

def test_r9_returns_string_when_regressing(adapter, monkeypatch):
    """T-EV-13-01: Regressing trend with ≥7 runs must return R9 adjustment string."""
    FakeMS = _make_fake_metric_store("regressing", 8, 52.1)

    # Inject fake MetricStore into briefing_adapter's lib.metric_store
    fake_lib = types.ModuleType("lib")
    fake_lib.metric_store = types.ModuleType("lib.metric_store")  # type: ignore[attr-defined]
    fake_lib.metric_store.MetricStore = FakeMS  # type: ignore[attr-defined]
    sys.modules["lib"] = fake_lib
    sys.modules["lib.metric_store"] = fake_lib.metric_store  # type: ignore[attr-defined]

    try:
        result = adapter._r9_quality_regression(runs=[{}] * 8, min_runs=7)
    finally:
        sys.modules.pop("lib", None)
        sys.modules.pop("lib.metric_store", None)

    assert result is not None, "Expected R9 string, got None"
    assert "regressing" in result
    assert "52.1" in result


# ===========================================================================
# T-EV-13-02: stable trend → returns None
# ===========================================================================

def test_r9_returns_none_when_stable(adapter, monkeypatch):
    """T-EV-13-02: Stable trend must return None (no regression)."""
    FakeMS = _make_fake_metric_store("stable", 10, 78.5)
    fake_lib = types.ModuleType("lib")
    fake_lib.metric_store = types.ModuleType("lib.metric_store")  # type: ignore[attr-defined]
    fake_lib.metric_store.MetricStore = FakeMS  # type: ignore[attr-defined]
    sys.modules["lib"] = fake_lib
    sys.modules["lib.metric_store"] = fake_lib.metric_store  # type: ignore[attr-defined]

    try:
        result = adapter._r9_quality_regression(runs=[{}] * 10, min_runs=7)
    finally:
        sys.modules.pop("lib", None)
        sys.modules.pop("lib.metric_store", None)

    assert result is None, f"Expected None for stable trend, got: {result}"


# ===========================================================================
# T-EV-13-03: run_count < min_runs=7 → returns None
# ===========================================================================

def test_r9_returns_none_when_insufficient_runs(adapter):
    """T-EV-13-03: Fewer than 7 runs must return None even if regressing."""
    FakeMS = _make_fake_metric_store("regressing", 4, 45.0)
    fake_lib = types.ModuleType("lib")
    fake_lib.metric_store = types.ModuleType("lib.metric_store")  # type: ignore[attr-defined]
    fake_lib.metric_store.MetricStore = FakeMS  # type: ignore[attr-defined]
    sys.modules["lib"] = fake_lib
    sys.modules["lib.metric_store"] = fake_lib.metric_store  # type: ignore[attr-defined]

    try:
        result = adapter._r9_quality_regression(runs=[{}] * 4, min_runs=7)
    finally:
        sys.modules.pop("lib", None)
        sys.modules.pop("lib.metric_store", None)

    assert result is None, f"Expected None for low run count, got: {result}"


# ===========================================================================
# T-EV-13-04: MetricStore raises exception → returns None
# ===========================================================================

def test_r9_returns_none_on_metric_store_exception(adapter):
    """T-EV-13-04: Exception in MetricStore must be caught and return None."""

    class BrokenMetricStore:
        def __init__(self, _root_dir):
            raise RuntimeError("MetricStore unavailable")

    fake_lib = types.ModuleType("lib")
    fake_lib.metric_store = types.ModuleType("lib.metric_store")  # type: ignore[attr-defined]
    fake_lib.metric_store.MetricStore = BrokenMetricStore  # type: ignore[attr-defined]
    sys.modules["lib"] = fake_lib
    sys.modules["lib.metric_store"] = fake_lib.metric_store  # type: ignore[attr-defined]

    try:
        result = adapter._r9_quality_regression(runs=[{}] * 8, min_runs=7)
    finally:
        sys.modules.pop("lib", None)
        sys.modules.pop("lib.metric_store", None)

    assert result is None, f"Expected None for exception, got: {result}"
