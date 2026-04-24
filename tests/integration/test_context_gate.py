"""tests/integration/test_context_gate.py -- S-30 context gate integration test.

specs/steal.md §15.2.1
"""
from __future__ import annotations

from pathlib import Path

_ARTHA_DIR = Path(__file__).resolve().parent.parent.parent


def test_session_start_gate_policy_documented():
    """S-30: fetch.md must document the context gate and exclude heavy files.

    The S-30 gate prevents evidence_lake and reflect-history.md from being
    loaded at session start. This test verifies the gate policy is documented
    in config/workflow/fetch.md.
    """
    fetch_md = _ARTHA_DIR / "config" / "workflow" / "fetch.md"
    assert fetch_md.exists(), "config/workflow/fetch.md must exist"

    content = fetch_md.read_text(encoding="utf-8")
    assert "S-30" in content, "fetch.md must document the S-30 context gate"
    assert "evidence_lake" in content, (
        "fetch.md must explicitly exclude evidence_lake from session start"
    )
    assert "reflect-history.md" in content, (
        "fetch.md must explicitly exclude reflect-history.md from session start"
    )
    assert "35" in content, "fetch.md must specify the 35K token budget"


def test_session_recap_under_budget():
    """S-30 / S-03: The per-session recap addition must stay well under 35K tokens."""
    import sys

    sys.path.insert(0, str(_ARTHA_DIR / "scripts" / "lib"))
    from context_budget import check_budget, estimate_token_count

    # Representative session recap (mirrors write_session_recap output)
    sample_recap = """\
written_at: '2026-04-22T09:00:00+00:00'
worked_on:
- immigration status check
- finance review
- goals sprint planning
status_changes:
- Filed I-485
- Paid Q1 taxes
decisions:
- Use USCIS attorney
- Skip Europe trip in Q3
next_actions:
- Follow up with lawyer by Friday
- Review open items list
- Check visa validity
"""
    budget = 35_000
    assert check_budget(sample_recap, budget), (
        f"Session recap uses {estimate_token_count(sample_recap)} tokens, "
        f"exceeds {budget} token budget"
    )
