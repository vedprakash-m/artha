"""Synthetic recurrence evidence and parity of both preflight surfaces."""
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from lib import loop_detector as detector


@pytest.fixture(params=["package", "script"])
def surface(request, monkeypatch, tmp_path):
    import preflight.state_checks as checks
    monkeypatch.setattr(checks, "ARTHA_DIR", str(tmp_path))
    if request.param == "package":
        import preflight as module
    else:
        path = Path(__file__).resolve().parents[2] / "scripts/preflight.py"
        spec = importlib.util.spec_from_file_location("_stuck_preflight", path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ARTHA_DIR", str(tmp_path))
    return module


def write_history(tmp_path, history):
    path = tmp_path / "state/catch_up_runs.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(yaml.safe_dump(history), encoding="utf-8")
    return path


def test_counts_do_not_supply_ids_or_change_list_recurrence():
    history = [{"items_surfaced": 8, "items_closed": 2, "items_corrected": 1}]
    history += [{"items_surfaced": ["OI-001", "OI-002"]}] * 3
    history += [{"items_closed": ["OI-002"], "items_corrected": ["OI-001"]}]
    assert detector.all_surfaced_items(history) == {"OI-001", "OI-002"}
    stuck, = detector.find_stuck_items(history)
    assert (stuck.item_id, stuck.appearances, stuck.corrections) == ("OI-001", 3, 1)
    assert detector.item_recurrence_score("OI-002", history).closures == 1


@pytest.mark.parametrize("history", [[], [{}], [{"items_surfaced": 0}], [{"items_surfaced": 5}]])
def test_no_item_evidence_is_unavailable(surface, tmp_path, history):
    write_history(tmp_path, history)
    result = surface.check_stuck_items()
    assert not result.passed
    assert result.severity == "P2"
    assert "unavailable" in result.message


@pytest.mark.parametrize("ids,expected", [([], "No stuck"), (["OI-001"], "Stuck items detected")])
def test_explicit_history_retains_outcomes(surface, tmp_path, ids, expected):
    write_history(tmp_path, [{"items_surfaced": ids}] * 3)
    result = surface.check_stuck_items()
    assert result.passed
    assert expected in result.message


def test_mixed_history_discloses_limited_evidence(surface, tmp_path):
    write_history(tmp_path, [{"items_surfaced": ["OI-001"]}] * 3 + [{"items_surfaced": 7}])
    result = surface.check_stuck_items()
    assert not result.passed
    assert "OI-001" in result.message
    assert "incomplete" in result.message


@pytest.mark.parametrize("bad", ["CANARY", True, -1, 1.5, {}, ["OI-001", 3]])
def test_malformed_fields_fail_without_content(surface, tmp_path, bad):
    write_history(tmp_path, [{"items_surfaced": bad}])
    result = surface.check_stuck_items()
    assert not result.passed
    assert result.severity == "P2"
    assert "CANARY" not in result.message


@pytest.mark.parametrize("history", [{"CANARY": "bad root"}, ["CANARY"]])
def test_malformed_history_is_not_healthy(surface, tmp_path, history):
    write_history(tmp_path, history)
    assert not surface.check_stuck_items().passed


def test_detector_exception_is_bounded(surface, tmp_path, monkeypatch):
    write_history(tmp_path, [{"items_surfaced": []}])
    def fail(*args):
        raise RuntimeError("CANARY private exception")
    monkeypatch.setattr(detector, "find_stuck_items", fail)
    result = surface.check_stuck_items()
    assert not result.passed
    assert "CANARY" not in result.message
    assert surface.format_results([result])[1]  # P2 does not block the Catch-up.


def test_missing_or_invalid_yaml_is_unavailable(surface, tmp_path):
    assert not surface.check_stuck_items().passed
    path = write_history(tmp_path, [])
    path.write_text("[CANARY", encoding="utf-8")
    assert not surface.check_stuck_items().passed


def test_default_loader_and_empty_badge(tmp_path, monkeypatch):
    monkeypatch.setattr(detector, "_REPO_ROOT", tmp_path)
    write_history(tmp_path, [{"items_surfaced": []}])
    assert detector.load_briefing_history() == [{"items_surfaced": []}]
    assert detector.format_stuck_items_badge([]) == ""


def test_nonrecord_direct_scoring_keeps_legacy_skip():
    assert detector.all_surfaced_items([None]) == set()
    assert detector.item_recurrence_score("OI-001", [None]).appearances == 0


@pytest.mark.parametrize("field", ["items_closed", "items_corrected"])
def test_invalid_auxiliary_fields_are_not_ignored(surface, tmp_path, field):
    write_history(tmp_path, [{"items_surfaced": [], field: "CANARY"}])
    assert not surface.check_stuck_items().passed
