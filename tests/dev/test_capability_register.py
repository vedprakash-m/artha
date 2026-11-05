"""RA-10: the generated register cannot drift, and every listed file exists."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from catchup_core.capabilities import CAPABILITIES, manifest, static_status  # noqa: E402


def test_register_is_current():
    result = subprocess.run([sys.executable, str(REPO / "tools" / "gen_capability_register.py"), "--check"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_manifest_matches_lf_implementation(tmp_path):
    previous = json.loads((REPO / "docs" / "spec-manifest.json").read_text(encoding="utf-8"))
    paths = {
        path for acceptance in previous["acceptance"].values()
        for path in (*acceptance["modules"], *acceptance["tests"])
    }
    paths.update(case["fixture"].split("::")[0] for case in previous["boundary_cases"].values())
    for pattern in ("migrations/*.sql", "prompts/*.md"):
        paths.update(path.relative_to(REPO).as_posix()
                     for path in (REPO / "scripts" / "catchup_core").glob(pattern))
    for relative in paths:
        source = REPO / relative
        if source.is_file():
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))
    assert manifest(tmp_path, previous)["implementation_sha256"] == previous["implementation_sha256"]


def test_every_non_later_capability_has_code_and_tests():
    missing = [c.id for c in CAPABILITIES if c.scope != "later" and not all(static_status(REPO, c))]
    assert not missing, missing


def test_ids_are_unique_and_cover_ra01_to_ra25():
    ids = [c.id for c in CAPABILITIES]
    assert len(ids) == len(set(ids)) and ids == [f"RA-{n:02d}" for n in range(1, 26)]
