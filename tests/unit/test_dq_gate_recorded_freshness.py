"""Recorded timestamps, not clock anomalies, determine Work freshness credit."""
from datetime import datetime, timezone

import pytest

from lib import dq_gate


@pytest.mark.parametrize("stamp,freshness", [
    (None, 0.0),
    ("invalid", 0.0),
    ("2026-11-03T12:00:00", 0.0),
    ("2026-11-04T12:00:00Z", 0.0),
    ("2026-11-03T12:00:00Z", 1.0),
    ("2026-11-02T12:00:00Z", 1.0 - 1.0 / 14),
])
def test_file_quality_requires_valid_nonfuture_recorded_age(tmp_path, monkeypatch, stamp, freshness):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 3, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(dq_gate, "datetime", Clock)
    target = tmp_path / "synthetic.md"
    fm = f"last_updated: {stamp}\n" if stamp is not None else ""
    target.write_text("---\n" + fm + "---\n" + "synthetic " * 200, encoding="utf-8")
    weights = dq_gate._DQ_DOMAIN_WEIGHTS["default"]
    expected = weights["A"] * 0.60 + weights["F"] * freshness + weights["C"]
    assert dq_gate.file_quality(target, "default") == pytest.approx(expected)
