"""tests/unit/test_preflight_eval_alerts.py — Unit test for EV-14 preflight check.

Tests check_eval_alerts() in scripts/preflight.py.
Ref: specs/eval.md EV-14, T-EV-14-01
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
def preflight():
    return _load_module("preflight_eval", _SCRIPTS_DIR / "preflight.py")


# ===========================================================================
# T-EV-14-01: unacked P0 alert in eval_alerts.yaml → check returns not-ok
# ===========================================================================

def test_unacked_p0_alert_blocks_catchup(preflight, tmp_path, monkeypatch):
    """T-EV-14-01: An unacked P0 eval alert must cause check_eval_alerts to return not-ok."""
    import yaml  # type: ignore[import]

    alerts_file = tmp_path / "eval_alerts.yaml"
    alerts_file.write_text(
        yaml.dump({
            "alerts": [
                {
                    "code": "EV-SH-01",
                    "severity": "P0",
                    "message": "Quality scorer crash",
                    "acked": False,
                }
            ]
        })
    )
    monkeypatch.setattr(preflight, "ARTHA_DIR", str(tmp_path))

    result = preflight.check_eval_alerts()
    # CheckResult.ok must be False for unacked P0
    assert result.ok is False, f"Expected ok=False for unacked P0, got: {result}"
    assert result.severity == "P0"
