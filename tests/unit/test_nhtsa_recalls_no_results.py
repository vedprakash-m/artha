"""NHTSA returns HTTP 400 with Count 0 when a vehicle has no recalls; that is a clean result, not an error."""
from __future__ import annotations

import requests

from scripts.skills.nhtsa_recalls import NHTSARecallSkill

VEHICLE = [{"make": "MAZDA", "model": "CX-50", "year": "2026", "vin": ""}]


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def _run(monkeypatch, status, body):
    monkeypatch.setattr(requests, "get", lambda url, timeout=10: FakeResponse(status, body))
    skill = NHTSARecallSkill(vehicles=VEHICLE)
    return skill.parse(skill.pull())["MAZDA_CX-50"]


def test_http_400_with_count_zero_means_no_recalls(monkeypatch):
    out = _run(monkeypatch, 400, {"Count": 0, "Message": "Results returned successfully", "results": []})
    assert out["recall_count"] == 0 and "status" not in out


def test_http_200_still_parses_recalls(monkeypatch):
    out = _run(monkeypatch, 200, {"Count": 1, "results": [{"NHTSACampaignNumber": "25V1"}]})
    assert out["recall_count"] == 1 and out["recall_ids"] == ["25V1"]


def test_other_400s_and_non_json_bodies_are_still_errors(monkeypatch):
    assert _run(monkeypatch, 400, {"message": "bad request"})["status"] == "error"
    assert _run(monkeypatch, 500, None)["status"] == "error"
