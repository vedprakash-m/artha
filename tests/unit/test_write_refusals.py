from __future__ import annotations

import json

import pytest

from lib import write_refusals


def test_unreadable_refusal_marker_is_unknown(tmp_path, monkeypatch):
    marker = tmp_path / "write-refusals.json"
    marker.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(write_refusals, "refusal_path", lambda: marker)

    with pytest.raises(write_refusals.RefusalStateUnknown, match="unreadable"):
        write_refusals.list_refusals()


def test_refusals_merge_under_fence(tmp_path, monkeypatch):
    marker = tmp_path / "write-refusals.json"
    monkeypatch.setattr(write_refusals, "refusal_path", lambda: marker)
    write_refusals.record_refusal(tmp_path / "goals.md", "stale")
    write_refusals.record_refusal(tmp_path / "items.md", "conflict")

    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert len(payload["refusals"]) == 2
