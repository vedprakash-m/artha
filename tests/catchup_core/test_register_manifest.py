"""The register and manifest are rendered from code, so they can be checked without the repository tooling."""
from __future__ import annotations

import json
from pathlib import Path

from catchup_core.capabilities import CAPABILITIES, manifest, render_register

REPO = Path(__file__).resolve().parents[2]


def test_register_lists_every_capability_with_present_or_absent_evidence():
    text = render_register(REPO)
    assert text.count("\n| RA-") == len(CAPABILITIES)
    assert "| RA-01 |" in text and "present" in text and "generated" in text


def test_manifest_maps_acceptance_ids_and_pins_implementation_bytes(tmp_path):
    first = manifest(REPO)
    assert first["acceptance"]["RA-01.AC1"]["capability"] == "RA-01" and first["contract_version"]
    assert json.dumps(first)                                            # serializable
    fake = tmp_path
    (fake / "specs").mkdir()
    (fake / "specs" / "re-artha.md").write_text("spec")
    with_spec = manifest(fake)
    assert len(with_spec["spec_sha256"]) == 64
    without = manifest(tmp_path / "nowhere", {"spec_sha256": "kept"})
    assert without["spec_sha256"] == "kept"                             # CI has no spec: the recorded digest is kept


def test_traceability_register_covers_every_requirement_and_every_prd_group():
    from catchup_core.capabilities import FR_DISPOSITION, render_traceability
    text = render_traceability(REPO)
    assert all(f"| {c.id} " in text for c in CAPABILITIES)
    assert all(group in text for group, *_ in FR_DISPOSITION) and "(missing)" not in text
