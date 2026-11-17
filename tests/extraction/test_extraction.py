"""TR-2: Independent synthetic extraction tests (prompt -> parse_output -> validation -> state)."""
from __future__ import annotations

import json
from pathlib import Path

from catchup_core import domain_pack as dp
from catchup_core import extract as ex
import kb_lint

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_immigration_fixtures_and_prompt_contracts():
    emails = [
        json.loads(line)
        for line in (FIXTURES_DIR / "mock_emails.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(emails) == 2
    assert emails[0]["subject"] == "Receipt Notice for I-485"
    assert "IOE0123456789" in emails[0]["body_text"]

    prompt = (REPO_ROOT / "prompts" / "immigration.md").read_text(encoding="utf-8")
    assert "## Role" in prompt or "Immigration" in prompt


def test_immigration_pack_extraction_grounds_quotes_and_rejects_ungrounded():
    pack = dp.load_pack_file(REPO_ROOT / "scripts" / "catchup_core" / "packs" / "immigration.yaml")
    emails = [
        json.loads(line)
        for line in (FIXTURES_DIR / "mock_emails.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    body = emails[1]["body_text"]
    quote = "H-1B extension has been approved"
    span = [body.index(quote), body.index(quote) + len(quote)]
    sig = ex.NormalizedSignal("sig-456", "immigration_email", body, "attorney_email", emails[1]["date"])
    prompt = ex.build_request(pack, sig, ["owner"]).prompt
    assert "<untrusted>" in prompt and quote in prompt

    valid_json = json.dumps({
        "complete": True,
        "candidates": [
            {"predicate": "case_status", "value": "approved", "subject_id": "owner", "quote": quote, "span": span}
        ],
    })
    parsed = ex.parse_output(valid_json, pack, sig, ["owner"])
    assert parsed.complete is True
    assert len(parsed.candidates) == 1
    assert parsed.candidates[0].value == "approved"

    ungrounded = json.dumps({
        "complete": True,
        "candidates": [
            {"predicate": "case_status", "value": "denied", "subject_id": "owner", "quote": "fabricated", "span": [0, 10]}
        ],
    })
    rejected = ex.parse_output(ungrounded, pack, sig, ["owner"])
    assert rejected.candidates == ()


def test_expected_immigration_state_validates_and_drift_fails(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # Provide valid baseline files so kb_lint does not enter bootstrap-skip mode.
    for filler in ("home.md", "travel.md", "digital.md"):
        (state_dir / filler).write_text(
            "---\nschema_version: \"1.0\"\nlast_updated: \"2026-11-01\"\nsensitivity: standard\n---\n\nOK\n",
            encoding="utf-8",
        )
    expected_md = (FIXTURES_DIR / "expected_immigration.md").read_text(encoding="utf-8")
    (state_dir / "immigration.md").write_text(expected_md, encoding="utf-8")

    # Legacy golden fixture predates required `sensitivity` field; adding it yields 0 P1 errors.
    legacy = kb_lint.run_lint(state_dir=state_dir, passes={"P1"})
    assert [f.pass_id for f in legacy.errors] == ["P1-missing-sensitivity"]

    compliant = expected_md.replace("schema_version: \"1.1\"\n", "schema_version: \"1.1\"\nsensitivity: restricted\n", 1)
    (state_dir / "immigration.md").write_text(compliant, encoding="utf-8")
    clean = kb_lint.run_lint(state_dir=state_dir, passes={"P1"})
    assert clean.files_scanned == 4
    assert clean.errors == []

    # Real schema drift triggers P1 error on lint, not a trivial string assertion
    (state_dir / "immigration.md").write_text("# Immigration\n- Case: Pending\n", encoding="utf-8")
    drifted = kb_lint.run_lint(state_dir=state_dir, passes={"P1"})
    assert any(f.pass_id == "P1-no-frontmatter" for f in drifted.errors)
