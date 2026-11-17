"""Verify docs/test-ownership.yaml parity, requirement mappings, and 5-pillar attribution."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("tools_test_ownership", ROOT / "tools" / "test_ownership.py")
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["tools_test_ownership"] = _MOD
_SPEC.loader.exec_module(_MOD)

PILLARS = _MOD.PILLARS
REQUIREMENTS = _MOD.REQUIREMENTS
VALID_DISPOSITIONS = _MOD.VALID_DISPOSITIONS
_tracked_test_paths = _MOD._tracked_test_paths
resolve_ownership = _MOD.resolve_ownership

ARTHA_TEST_OWNER = {
    "requirements": ["INFRA-CI", "RA-10"],
    "modules": ["tools/test_ownership.py"],
}


def test_ownership_yaml_is_current_and_boundary_cases_resolve():
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "test_ownership.py"), "--check"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_every_tracked_public_test_has_valid_requirement_and_pillar():
    tracked = set(_tracked_test_paths(ROOT))
    ownership = resolve_ownership(ROOT)
    assert set(ownership) == tracked
    assert tracked, "expected tracked public test files"

    valid_pillars = set(PILLARS)
    for rel, meta in ownership.items():
        reqs = meta["requirements"]
        assert reqs, f"{rel}: empty requirements"
        assert all(r in REQUIREMENTS for r in reqs), f"{rel}: unknown requirement in {reqs}"
        assert set(meta["dispositions"]) <= VALID_DISPOSITIONS, f"{rel}: invalid disposition"
        assert set(meta["pillars"]) <= valid_pillars and meta["pillars"], f"{rel}: invalid pillars"


def test_tr1_and_tr3_have_mapped_tests():
    ownership = resolve_ownership(ROOT)
    tr1 = [p for p, m in ownership.items() if "TR-1" in m["requirements"]]
    tr3 = [p for p, m in ownership.items() if "TR-3" in m["requirements"]]
    assert len(tr1) >= 4
    assert len(tr3) >= 2
