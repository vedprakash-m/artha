"""tests/eval/test_correction_injection.py — Integration test for correction_feeder filtering.

Verifies that a valid correction fact written to the in-memory facts list
passes through _filter_facts() and appears in the filtered output.
Ref: specs/eval.md EV-12, T-EV-12-07
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def feeder():
    return _load_module("correction_feeder_inj", _SCRIPTS_DIR / "correction_feeder.py")


# ===========================================================================
# T-EV-12-07: Valid correction fact passes through _filter_facts()
# ===========================================================================

def test_valid_correction_passes_filter(feeder):
    """T-EV-12-07: A valid correction fact must survive _filter_facts()."""
    future_ttl = (date.today() + timedelta(days=365)).isoformat()
    facts = [
        {
            "type": "correction",
            "domain": "finance",
            "value": "Use net income, not gross, for DTI ratio calculation",
            "ttl": future_ttl,
        }
    ]
    result = feeder._filter_facts(facts, domain="finance")
    assert len(result) == 1, (
        f"Expected 1 fact to pass _filter_facts(), got {len(result)}: {result}"
    )
    assert result[0]["domain"] == "finance"
    assert result[0]["type"] == "correction"
