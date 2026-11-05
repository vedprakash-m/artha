"""Both preflight surfaces must not hide missing or future planning freshness."""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(params=["package", "script"])
def preflight(request, monkeypatch, tmp_path):
    if request.param == "package":
        import preflight.state_checks as module
        monkeypatch.setattr(module, "STATE_DIR", tmp_path)
    else:
        name = "_preflight_planning_signals_script"
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / "preflight.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        monkeypatch.setattr(module, "STATE_DIR", str(tmp_path))
        monkeypatch.setattr(module, "SCRIPTS_DIR", str(SCRIPTS))

    class FrozenDate(date):
        @classmethod
        def today(cls):
            return date(2026, 4, 15)

    monkeypatch.setattr(module, "date", FrozenDate)
    return module


def write_active_signals(path: Path, last_updated: str | None) -> None:
    updated = f'last_updated: "{last_updated}"\n' if last_updated is not None else ""
    path.write_text(
        "---\n"
        "schema_version: 1\n"
        "domain: planning_signals\n"
        f"{updated}"
        "signals:\n"
        "  - id: SIG-001\n"
        "    entity_key: synthetic_opportunity\n"
        "    text: Synthetic opportunity\n"
        "    domain: learning\n"
        "    archetype: opportunity\n"
        "    first_detected: '2026-04-14'\n"
        "    last_seen: '2026-04-14'\n"
        "    detection_count: 2\n"
        "    materialized: false\n"
        "    materialization_threshold: 2\n"
        "    evidence:\n"
        "      - '2026-04-14: test — synthetic evidence'\n"
        "    candidate_type: scenario\n"
        "    candidate_title: Synthetic opportunity\n"
        "---\n"
        "# Planning Signals\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("last_updated", "passed"),
    [
        ("2026-04-14", True),
        ("2026-04-13", False),
        ("2026-04-16", False),
        (None, False),
        ("not-a-date", False),
    ],
)
def test_planning_signal_freshness_is_truthful_on_both_preflight_surfaces(
    preflight, tmp_path, last_updated, passed,
):
    write_active_signals(tmp_path / "planning_signals.md", last_updated)

    result = preflight.check_planning_signals()

    assert result.severity == "P1"
    assert result.passed is passed
    if last_updated == "2026-04-13":
        assert "2+ days" in result.message
    if last_updated == "2026-04-16":
        assert "future" in result.message.lower() or "invalid" in result.message.lower()
    if last_updated is None:
        assert "last_updated" in result.message


def test_stale_planning_signals_without_active_entries_are_not_reported_stale(
    preflight, tmp_path,
):
    path = tmp_path / "planning_signals.md"
    write_active_signals(path, "2026-04-10")
    path.write_text(
        path.read_text(encoding="utf-8").replace("materialized: false", "materialized: true"),
        encoding="utf-8",
    )

    result = preflight.check_planning_signals()

    assert result.passed
