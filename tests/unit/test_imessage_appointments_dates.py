"""iMessage appointment texts: relative dates anchor to the send date, and edited items are not duplicated.

The skill is private (gitignored), so these tests skip wherever it is not present.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

ia = pytest.importorskip("skills.imessage_appointments")

TODAY = date(2026, 11, 18)
PHONE = "+15550100"          # fake number
OPEN_ITEMS = "---\ndomain: open_items\n---\n\n# Open Items\n\n## Active\n\n## Closed\n"


def _ts(dt: datetime) -> float:
    """Apple-epoch seconds, the format the skill's _ts_to_dt decodes."""
    return dt.timestamp() - ia._APPLE_EPOCH_OFFSET


def _skill(tmp_path, monkeypatch):
    monkeypatch.setattr(ia, "_pacific_today", lambda: TODAY)
    monkeypatch.setattr(ia, "_KNOWN_PROVIDERS", {PHONE: "Test Dental Clinic"})
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "open_items.md").write_text(OPEN_ITEMS)
    return ia.IMessageAppointmentsSkill(artha_dir=str(tmp_path))


def _oi_text(tmp_path):
    return (tmp_path / "state" / "open_items.md").read_text()


def _row(body, sent_at):
    return {"phone": PHONE, "body": body, "sent": False, "ts": _ts(sent_at)}


def test_today_in_an_old_text_is_dated_to_the_send_day_and_is_skipped(tmp_path, monkeypatch):
    skill = _skill(tmp_path, monkeypatch)
    row = _row("Reminder: your dental appointment is today at 3:30 PM.", datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc))
    out = skill.parse([row])
    assert out["appointments_detected"] == 0 and out["ois_created"] == 0   # Nov 15 is three days past, not "today"


def test_today_in_a_fresh_text_is_today(tmp_path, monkeypatch):
    skill = _skill(tmp_path, monkeypatch)
    row = _row("Reminder: your dental appointment is today at 3:30 PM.", datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc))
    assert skill.parse([row])["ois_created"] == 1
    assert "deadline: 2026-11-18" in _oi_text(tmp_path)


def test_known_clinic_numbers_resolve_to_names(tmp_path, monkeypatch):
    skill = _skill(tmp_path, monkeypatch)
    row = _row("Reminder: appointment on 2026-11-20 at 11:00 AM", datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc))
    assert skill.parse([row])["ois_created"] == 1
    assert "Test Dental Clinic appointment on 2026-11-20" in _oi_text(tmp_path)


def test_an_item_with_an_edited_provider_is_not_duplicated():
    appt = {"provider": PHONE, "date": "2026-11-20", "time": "11:00"}
    oi = {"via": ia._VIA_TAG,
          "description": "Child - Test Dental Clinic appointment on 2026-11-20 at 11:00. Source: iMessage confirmation."}
    assert ia._oi_matches_appt(oi, appt) is True


def test_a_different_time_is_a_different_appointment():
    appt = {"provider": PHONE, "date": "2026-11-20", "time": "14:00"}
    oi = {"via": ia._VIA_TAG, "description": "Child - Test Dental Clinic appointment on 2026-11-20 at 11:00."}
    assert ia._oi_matches_appt(oi, appt) is False


def test_items_closed_by_hand_are_left_alone_and_not_listed_as_open(tmp_path, monkeypatch):
    skill = _skill(tmp_path, monkeypatch)
    block = ("- id: OI-001\n  date_added: 2026-11-01\n  source_domain: calendar\n"
             "  description: \"Old appointment on 2026-11-10.\"\n  deadline: 2026-11-10\n  priority: P1\n"
             f"  status: expired\n  via: {ia._VIA_TAG}\n  todo_id: \"\"\n  closed_date: 2026-11-18\n"
             "  closed_notes: \"closed by hand\"\n\n")
    p = tmp_path / "state" / "open_items.md"
    p.write_text(OPEN_ITEMS.replace("## Active\n", "## Active\n\n" + block))
    before = p.read_text()
    for _ in range(2):                                    # running twice must not change anything
        out = skill.parse([])
    assert p.read_text() == before
    assert out["ois_closed"] == 0 and out["open_appointments"] == []
