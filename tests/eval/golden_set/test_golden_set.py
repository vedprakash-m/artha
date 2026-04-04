"""tests/eval/golden_set/test_golden_set.py — Parametrized golden-set regression tests.

Loads fixtures from fixtures.yaml and runs each through eval_scorer to verify
that quality scores meet the expected thresholds.

All briefing content is 100% fictional — no real user data (DD-5).
Ref: specs/eval.md EV-12, tests/eval/rubric.yaml
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_eval_scorer = None


def _get_eval_scorer():
    global _eval_scorer
    if _eval_scorer is None:
        _eval_scorer = _load_module("eval_scorer", _SCRIPTS_DIR / "eval_scorer.py")
    return _eval_scorer


# ---------------------------------------------------------------------------
# Load fixtures from YAML
# ---------------------------------------------------------------------------

_FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures.yaml"


def _load_fixtures() -> list[dict]:
    try:
        import yaml
    except ImportError:
        pytest.skip("PyYAML not available")
        return []
    data = yaml.safe_load(_FIXTURES_PATH.read_text(encoding="utf-8"))
    return data.get("fixtures", [])


def _fixture_ids() -> list[str]:
    try:
        import yaml

        data = yaml.safe_load(_FIXTURES_PATH.read_text(encoding="utf-8"))
        return [f["id"] for f in data.get("fixtures", [])]
    except Exception:
        return []


_ALL_FIXTURES = _load_fixtures()
_GOLDEN = [f for f in _ALL_FIXTURES if f.get("expected") == "pass"]
_ANTI_GOLDEN = [f for f in _ALL_FIXTURES if f.get("expected") == "fail"]


# ---------------------------------------------------------------------------
# Golden tests — expected to pass quality threshold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    _GOLDEN,
    ids=[f["id"] for f in _GOLDEN],
)
def test_golden_quality_score(fixture, tmp_path):
    """Golden fixtures must score above their min_quality_score."""
    scorer = _get_eval_scorer()
    path = tmp_path / f"{fixture['id']}.md"
    path.write_text(fixture["briefing"], encoding="utf-8")

    result = scorer.score_briefing(path)
    threshold = fixture.get("min_quality_score", 60)

    assert result["quality_score"] is not None, f"Scorer returned None for {fixture['id']}"
    assert result["quality_score"] >= threshold, (
        f"{fixture['id']}: scored {result['quality_score']:.1f}, "
        f"expected >= {threshold}. Dimensions: {result['dimensions']}"
    )


@pytest.mark.parametrize(
    "fixture",
    _GOLDEN,
    ids=[f["id"] for f in _GOLDEN],
)
def test_golden_dimension_checks(fixture, tmp_path):
    """Golden fixtures must meet per-dimension minimums."""
    scorer = _get_eval_scorer()
    path = tmp_path / f"{fixture['id']}.md"
    path.write_text(fixture["briefing"], encoding="utf-8")

    result = scorer.score_briefing(path)
    checks = fixture.get("dimension_checks", {})

    for dim, bounds in checks.items():
        actual = result["dimensions"].get(dim)
        assert actual is not None, f"{fixture['id']}: dimension '{dim}' missing"
        if "min" in bounds:
            assert actual >= bounds["min"], (
                f"{fixture['id']}: {dim} = {actual:.1f}, expected >= {bounds['min']}"
            )


# ---------------------------------------------------------------------------
# Anti-golden tests — expected to score below threshold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    _ANTI_GOLDEN,
    ids=[f["id"] for f in _ANTI_GOLDEN],
)
def test_anti_golden_quality_score(fixture, tmp_path):
    """Anti-golden fixtures must score below their max_quality_score."""
    scorer = _get_eval_scorer()
    path = tmp_path / f"{fixture['id']}.md"
    path.write_text(fixture["briefing"], encoding="utf-8")

    result = scorer.score_briefing(path)
    threshold = fixture.get("max_quality_score", 40)

    assert result["quality_score"] is not None, f"Scorer returned None for {fixture['id']}"
    assert result["quality_score"] <= threshold, (
        f"{fixture['id']}: scored {result['quality_score']:.1f}, "
        f"expected <= {threshold}. Dimensions: {result['dimensions']}"
    )


@pytest.mark.parametrize(
    "fixture",
    _ANTI_GOLDEN,
    ids=[f["id"] for f in _ANTI_GOLDEN],
)
def test_anti_golden_dimension_checks(fixture, tmp_path):
    """Anti-golden fixtures must fail per-dimension maximums."""
    scorer = _get_eval_scorer()
    path = tmp_path / f"{fixture['id']}.md"
    path.write_text(fixture["briefing"], encoding="utf-8")

    result = scorer.score_briefing(path)
    checks = fixture.get("dimension_checks", {})

    for dim, bounds in checks.items():
        actual = result["dimensions"].get(dim)
        assert actual is not None, f"{fixture['id']}: dimension '{dim}' missing"
        if "max" in bounds:
            assert actual <= bounds["max"], (
                f"{fixture['id']}: {dim} = {actual:.1f}, expected <= {bounds['max']}"
            )
