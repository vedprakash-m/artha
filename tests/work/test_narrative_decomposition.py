"""Tests verifying the narrative subpackage decomposition (WS-3).

Ensures the facade re-exports are intact and all submodules are importable
with their expected public functions.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Facade tests
# ---------------------------------------------------------------------------


def test_facade_reexports_NarrativeEngine():
    import narrative_engine  # noqa: PLC0415

    assert hasattr(narrative_engine, "NarrativeEngine")


def test_facade_reexports_main():
    import narrative_engine  # noqa: PLC0415

    assert hasattr(narrative_engine, "main")
    assert callable(narrative_engine.main)


# ---------------------------------------------------------------------------
# Subpackage class hierarchy
# ---------------------------------------------------------------------------


def test_narrative_engine_is_NarrativeEngineBase_subclass():
    from narrative import NarrativeEngine
    from narrative._base import NarrativeEngineBase

    assert issubclass(NarrativeEngine, NarrativeEngineBase)


# ---------------------------------------------------------------------------
# Submodule importability
# ---------------------------------------------------------------------------


def test_memo_module_importable():
    from narrative import memo  # noqa: PLC0415

    assert hasattr(memo, "generate_weekly_memo")
    assert hasattr(memo, "generate_escalation_memo")
    assert hasattr(memo, "generate_decision_memo")


def test_connect_module_importable():
    from narrative import connect  # noqa: PLC0415

    assert hasattr(connect, "generate_connect_summary")
    assert hasattr(connect, "generate_calibration_brief")


def test_content_module_importable():
    from narrative import content  # noqa: PLC0415

    assert hasattr(content, "generate_newsletter")
    assert hasattr(content, "generate_deck")


def test_career_module_importable():
    from narrative import career  # noqa: PLC0415

    assert hasattr(career, "generate_promo_case")


def test_support_module_importable():
    from narrative import support  # noqa: PLC0415

    assert hasattr(support, "generate_talking_points")
    assert hasattr(support, "generate_boundary_report")
