"""tests/work/test_work_reader_contract.py — Phase 3: API contract snapshot.

Verifies that work_reader.py re-exports exactly the same public cmd_* functions
with the same signatures as before decomposition, so no downstream caller
(manual scripts, tests) silently breaks.

This is a "freeze test" — if any signature changes during Phase 3, this test
catches it immediately.

Ref: specs/pay-debt.md §7.1 Step 3b½, §7.6 T3-59
"""
from __future__ import annotations

import inspect
import sys

import pytest


# ---------------------------------------------------------------------------
# Authoritative API snapshot captured from work_reader before decomposition.
# Format: {name: signature_string}
# Only cmd_* functions and WorkBriefingConfig are frozen — internal helpers
# are not part of the public contract.
# ---------------------------------------------------------------------------
_EXPECTED_SIGNATURES: dict[str, str] = {
    "cmd_connect": "() -> 'str'",
    "cmd_connect_prep": "(mode: 'str' = '') -> 'str'",
    "cmd_day": "() -> 'str'",
    "cmd_decide": "(context: 'str') -> 'str'",
    "cmd_deck": "(topic: 'str' = '') -> 'str'",
    "cmd_docs": "() -> 'str'",
    "cmd_graph": "() -> 'str'",
    "cmd_health": "() -> 'str'",
    "cmd_incidents": "() -> 'str'",
    "cmd_journey": "(project: 'str' = '') -> 'str'",
    "cmd_live": "(meeting_id: 'str' = '') -> 'str'",
    "cmd_mark_preread": "(meeting_id: 'str') -> 'str'",
    "cmd_memo": "(period: 'str' = '', weekly: 'bool' = False, escalation_context: 'str' = '', decision_id: 'str' = '') -> 'str'",
    "cmd_newsletter": "(period: 'str' = '') -> 'str'",
    "cmd_people": "(query: 'str') -> 'str'",
    "cmd_prep": "(for_date: 'Optional[date]' = None) -> 'str'",
    "cmd_promo_case": "(narrative: 'bool' = False) -> 'str'",
    "cmd_pulse": "() -> 'str'",
    "cmd_repos": "() -> 'str'",
    "cmd_return": "(window: 'str' = '1d') -> 'str'",
    "cmd_sources": "(query: 'str' = '') -> 'str'",
    "cmd_sources_add": "(url: 'str', context: 'str' = '') -> 'str'",
    "cmd_sprint": "() -> 'str'",
    "cmd_talking_points": "(topic: 'str') -> 'str'",
    "cmd_work": "() -> 'str'",
    "main": "(argv: 'Optional[list[str]]' = None) -> 'int'",
}


def test_work_reader_public_api_contract():
    """All cmd_* functions and main() must be importable from work_reader with
    the same signatures captured before Phase 3 decomposition.

    If this test fails, a Phase 3 step introduced a signature change.
    """
    import work_reader  # noqa: PLC0415

    mismatches: list[str] = []
    missing: list[str] = []

    for name, expected_sig in _EXPECTED_SIGNATURES.items():
        obj = getattr(work_reader, name, None)
        if obj is None:
            missing.append(name)
            continue
        try:
            actual_sig = str(inspect.signature(obj))
        except (ValueError, TypeError):
            actual_sig = "(...)"
        if actual_sig != expected_sig:
            mismatches.append(
                f"{name}: expected {expected_sig!r}, got {actual_sig!r}"
            )

    errors: list[str] = []
    if missing:
        errors.append("Missing from work_reader:\n" + "\n".join(f"  - {n}" for n in missing))
    if mismatches:
        errors.append("Signature drift:\n" + "\n".join(f"  - {m}" for m in mismatches))

    assert not errors, "\n".join(errors)


def test_work_briefing_config_importable():
    """WorkBriefingConfig dataclass must be importable from work_reader."""
    import work_reader
    assert hasattr(work_reader, "WorkBriefingConfig")
    cfg = work_reader.WorkBriefingConfig()
    assert not cfg.flash_mode
