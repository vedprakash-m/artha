"""RA-10: the generated register cannot drift, and every listed file exists."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from catchup_core.capabilities import CAPABILITIES, static_status  # noqa: E402


def test_register_is_current():
    result = subprocess.run([sys.executable, str(REPO / "tools" / "gen_capability_register.py"), "--check"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_non_later_capability_has_code_and_tests():
    missing = [c.id for c in CAPABILITIES if c.scope != "later" and not all(static_status(REPO, c))]
    assert not missing, missing


def test_ids_are_unique_and_cover_ra01_to_ra25():
    ids = [c.id for c in CAPABILITIES]
    assert len(ids) == len(set(ids)) and ids == [f"RA-{n:02d}" for n in range(1, 26)]
