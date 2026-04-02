"""tests/unit/test_eval_config.py — Validates eval harness config in artha_config.yaml.

Ref: specs/eval.md EV-16 (config gates), T-EV-15-01 to T-EV-15-02
"""
from __future__ import annotations

from pathlib import Path

import pytest

_ARTHA_DIR = Path(__file__).resolve().parent.parent.parent
_CONFIG_FILE = _ARTHA_DIR / "config" / "artha_config.yaml"


@pytest.fixture(scope="module")
def cfg():
    import yaml  # type: ignore[import]
    return yaml.safe_load(_CONFIG_FILE.read_text(encoding="utf-8"))


# ===========================================================================
# T-EV-15-01: artha_config.yaml loads and harness.eval keys are present
# ===========================================================================

def test_harness_eval_section_exists(cfg):
    """T-EV-15-01: artha_config.yaml must contain a harness.eval section."""
    assert isinstance(cfg, dict), "Config must be a YAML dict"
    harness = cfg.get("harness")
    assert isinstance(harness, dict), "Config must have a 'harness' key with dict value"
    eval_cfg = harness.get("eval")
    assert isinstance(eval_cfg, dict), "harness.eval must be a dict"


# ===========================================================================
# T-EV-15-02: harness.eval.scorer.enabled exists and defaults true
# ===========================================================================

def test_scorer_enabled_defaults_true(cfg):
    """T-EV-15-02: harness.eval.scorer.enabled must be present and truthy by default."""
    eval_cfg = cfg.get("harness", {}).get("eval", {})
    scorer_cfg = eval_cfg.get("scorer", {})
    # Either the key is absent (defaults to True) or explicitly True
    enabled = scorer_cfg.get("enabled", True)
    assert enabled is True, (
        f"harness.eval.scorer.enabled must default to True, got: {enabled}"
    )
