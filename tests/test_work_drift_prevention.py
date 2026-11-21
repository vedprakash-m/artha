"""Regression tests: Work state writers stay inside the journaled boundary and drift is reported early."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from lib import work_publication  # noqa: E402
from preflight.work_privacy_checks import check_work_privacy_drift  # noqa: E402


class _Protocol:
    def __init__(self, *, enrolled=True, error=None):
        self._enrolled, self._error = enrolled, error

    def is_enrolled(self):
        return self._enrolled

    def current(self):
        if self._error:
            raise self._error
        return {"generation": 12}


def test_check_passes_when_not_enrolled():
    result = check_work_privacy_drift(lambda: _Protocol(enrolled=False))
    assert result.passed and result.severity == "P1"


def test_check_passes_when_current():
    result = check_work_privacy_drift(lambda: _Protocol())
    assert result.passed and "12" in result.message


def test_check_fails_with_fix_hint_on_drift():
    err = RuntimeError("unjournaled Work member drift requires reviewed reconciliation")
    result = check_work_privacy_drift(lambda: _Protocol(error=err))
    assert not result.passed and result.severity == "P1"
    assert "reconcile" in result.fix_hint


def test_check_reports_unavailable_without_raising():
    result = check_work_privacy_drift(lambda: (_ for _ in ()).throw(OSError("no key")))
    assert not result.passed and "not verified" in result.message


def test_quality_gate_log_lives_outside_journaled_roots():
    from lib import dq_gate

    log = Path(dq_gate._GATE_LOG).resolve()
    assert (REPO / "state" / "work") not in log.parents
    assert (Path.home() / ".artha-local" / "work") not in log.parents


def test_evidence_lake_publishes_through_work_publication(tmp_path, monkeypatch):
    from lib import evidence_lake

    seen = {}

    def fake_publish_text(path, text, *, legacy):
        seen["path"], seen["text"] = Path(path), text

    monkeypatch.setattr(work_publication, "publish_text", fake_publish_text)
    target = tmp_path / "lake.md"
    evidence_lake._publish_atomic(target, "hello")
    assert seen == {"path": target, "text": "hello"}
    assert not target.exists()  # nothing written outside the publication boundary


def test_evidence_lake_legacy_write_when_not_enrolled(tmp_path, monkeypatch):
    from lib import evidence_lake

    monkeypatch.setattr(work_publication, "_probe", lambda: None)
    target = tmp_path / "lake.md"
    evidence_lake._publish_atomic(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
