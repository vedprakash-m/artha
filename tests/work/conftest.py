"""
tests/work/conftest.py — Shared pytest fixtures for Work OS tests.

Patches all external provider availability checks on WorkLoop so that the
test suite does not invoke real CLI tools (agency, npx, az, outlookctl) that
may be installed on the host machine and could hang or cause side-effects.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


@pytest.fixture(autouse=True)
def _mock_work_loop_providers(monkeypatch):
    """
    Stub out all WorkLoop external provider checks so they return 'unavailable'.

    This prevents real CLI tool invocations (agency, npx, az, outlookctl) from
    running during the test suite.  Tests that specifically need provider
    availability should override this fixture locally.
    """
    try:
        import work_loop  # noqa: PLC0415
    except ImportError:
        return  # Not applicable for non-work-loop tests

    monkeypatch.setattr(work_loop.WorkLoop, "_check_agency_available",   lambda self: (False, ""))
    monkeypatch.setattr(work_loop.WorkLoop, "_check_workiq_available",   lambda self: False)
    monkeypatch.setattr(work_loop.WorkLoop, "_check_outlook_available",  lambda self: False)
    monkeypatch.setattr(work_loop.WorkLoop, "_check_ado_available",      lambda self: False)
    monkeypatch.setattr(work_loop.WorkLoop, "_check_graph_available",    lambda self: False)
