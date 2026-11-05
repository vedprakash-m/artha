"""Strict numeric bridge validation and preservation on rejected writes."""
from __future__ import annotations

import pytest

from schemas.bridge_schemas import (
    BridgeValidationError,
    make_work_load_pulse,
    validate_bridge_artifact,
    write_bridge_artifact,
)


@pytest.mark.parametrize("field,value", [
    ("boundary_score", True),
    ("boundary_score", float("nan")),
    ("boundary_score", float("inf")),
    ("focus_availability_score", False),
    ("focus_availability_score", float("nan")),
    ("total_meeting_hours", True),
    ("total_meeting_hours", float("inf")),
    ("after_hours_count", True),
    ("after_hours_count", 1.5),
])
def test_invalid_numeric_bridge_preserves_existing_artifact(tmp_path, field, value):
    pulse = make_work_load_pulse("2026-03-16", 6.5, 2, 0.4, 0)
    pulse[field] = value
    target = tmp_path / "work_load_pulse.json"
    original = b"previous generation"
    target.write_bytes(original)
    with pytest.raises(BridgeValidationError):
        write_bridge_artifact(target, "work_load_pulse", pulse)
    assert target.read_bytes() == original
    assert not target.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize("artifact", [None, [], "invalid", 1])
def test_nonobject_bridge_has_explicit_validation_failure(artifact):
    with pytest.raises(BridgeValidationError):
        validate_bridge_artifact("work_load_pulse", artifact)


def test_zero_bridge_metrics_are_valid():
    pulse = make_work_load_pulse("2026-03-16", 0, 0, 0, 0)
    validate_bridge_artifact("work_load_pulse", pulse)
    assert pulse["boundary_score"] == pulse["focus_availability_score"] == 0
