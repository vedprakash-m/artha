"""Parameterized synthetic contract tests for active and legacy connectors.

Exercises each connector through ``catchup_core.adapters.fetch_via_handler`` and
``schemas.connector_record.validate_record`` using synthetic temporary fixtures
(SQLite DBs, RSS XML, Health XML, mocked API responses).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from catchup_core.adapters import fetch_via_handler
from catchup_core.contracts import CoverageStatus
from connectors import (
    apple_health,
    canvas_lms,
    imessage_local,
    onenote,
    reddit,
    rss_feed,
    whatsapp_local,
)
from lib import connector_coverage

ARTHA_TEST_OWNER = {
    "requirements": ["RA-08", "RA-16", "TR-3"],
    "modules": [
        "scripts/connectors/imessage_local.py",
        "scripts/connectors/whatsapp_local.py",
        "scripts/connectors/rss_feed.py",
        "scripts/connectors/apple_health.py",
        "scripts/connectors/canvas_lms.py",
        "scripts/connectors/onenote.py",
        "scripts/connectors/reddit.py",
    ],
}


def _apple_ts_ns(days_ago: float = 0.1) -> int:
    apple_epoch = datetime(2001, 1, 1, tzinfo=timezone.utc)
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return int((dt - apple_epoch).total_seconds() * 1_000_000_000)


def _apple_ts_sec(days_ago: float = 0.1) -> float:
    apple_epoch = datetime(2001, 1, 1, tzinfo=timezone.utc)
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return (dt - apple_epoch).total_seconds()


def _make_imessage_env(tmp_path: Path, monkeypatch) -> dict:
    db = tmp_path / "chat.db"
    tmp_db = tmp_path / "chat_copy.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT)")
    con.execute("CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, display_name TEXT, group_id TEXT, chat_identifier TEXT)")
    con.execute("CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER)")
    con.execute("CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, date INTEGER, is_from_me INTEGER, handle_id INTEGER)")
    con.execute("INSERT INTO handle VALUES (1, '+15550100')")
    con.execute("INSERT INTO chat VALUES (1, 'Alex', NULL, 'chat-1')")
    con.execute("INSERT INTO message VALUES (10, 'Synthetic hello', ?, 0, 1)", (_apple_ts_ns(),))
    con.execute("INSERT INTO chat_message_join VALUES (1, 10)")
    con.commit()
    con.close()

    monkeypatch.setattr(imessage_local.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(imessage_local, "_IMESSAGE_DB", db)
    monkeypatch.setattr(imessage_local, "_TMP_DB", tmp_db)
    monkeypatch.setattr(imessage_local, "_resolve_contact_name", lambda c: "Alex")
    return {"fetch_kwargs": {"max_results": 10}, "expected_id": "imsg-10"}


def _make_whatsapp_env(tmp_path: Path, monkeypatch) -> dict:
    db = tmp_path / "ChatStorage.sqlite"
    tmp_db = tmp_path / "wa_copy.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZPARTNERNAME TEXT, ZCONTACTJID TEXT, ZSESSIONTYPE INTEGER)")
    con.execute("CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZMESSAGEDATE REAL, ZISFROMME INTEGER, ZTEXT TEXT)")
    con.execute("INSERT INTO ZWACHATSESSION VALUES (1, 'Sam', '15550101@s.whatsapp.net', 0)")
    con.execute("INSERT INTO ZWAMESSAGE VALUES (7, 1, ?, 0, 'Synthetic WhatsApp ping')", (_apple_ts_sec(),))
    con.commit()
    con.close()

    monkeypatch.setattr(whatsapp_local.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(whatsapp_local, "_MACOS_DB", db)
    monkeypatch.setattr(whatsapp_local, "_TMP_DB", tmp_db)
    return {"fetch_kwargs": {"max_results": 10}, "expected_id": "wa-7"}


def _make_rss_env(tmp_path: Path, monkeypatch) -> dict:
    rss_xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<title>Synthetic Feed</title>"
        "<item><title>Update 1</title><guid>rss-item-1</guid>"
        "<link>https://example.invalid/1</link>"
        "<description>Summary body</description></item>"
        "</channel></rss>"
    )
    monkeypatch.setattr(rss_feed, "_fetch_feed_text", lambda url, timeout=10: rss_xml)
    return {
        "auth_context": {"feeds": [{"url": "https://example.invalid/feed.xml", "tag": "rss", "domain": "learning"}]},
        "expected_id": "rss-item-1",
    }


def _make_apple_health_env(tmp_path: Path, monkeypatch) -> dict:
    xml_path = tmp_path / "export.xml"
    recent_start = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    xml_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<HealthData>"
        '<Record type="HKQuantityTypeIdentifierStepCount" sourceName="Watch" '
        f'unit="count" startDate="{recent_start}" value="4200"/>'
        "</HealthData>",
        encoding="utf-8",
    )
    return {
        "auth_context": {"file": str(xml_path)},
        "fetch_kwargs": {"max_results": 10},
        "expected_prefix": "ah-",
    }


ACTIVE_CONNECTORS = [
    ("imessage_local", imessage_local, _make_imessage_env),
    ("whatsapp_local", whatsapp_local, _make_whatsapp_env),
    ("rss_feed", rss_feed, _make_rss_env),
    ("apple_health", apple_health, _make_apple_health_env),
]


@pytest.mark.parametrize(("name", "mod", "setup_fn"), ACTIVE_CONNECTORS, ids=[c[0] for c in ACTIVE_CONNECTORS])
def test_active_connector_emits_valid_connector_records_via_adapter(tmp_path, monkeypatch, name, mod, setup_fn):
    monkeypatch.setattr(connector_coverage, "default_path", lambda: tmp_path / "cov" / "cov.json")
    spec = setup_fn(tmp_path, monkeypatch)
    res = fetch_via_handler(
        name,
        "personal",
        mod.fetch,
        since="30d",
        auth_context=spec.get("auth_context"),
        fetch_kwargs=spec.get("fetch_kwargs"),
    )
    assert res.error is None
    assert res.status is CoverageStatus.COMPLETE
    assert not any("rejected" in b for b in res.blind_spots)
    assert len(res.records) == 1
    rec = res.records[0]
    assert rec.occurred_at is not None
    if "expected_id" in spec:
        assert rec.upstream_id == spec["expected_id"]
    if "expected_prefix" in spec:
        assert rec.upstream_id.startswith(spec["expected_prefix"])


@pytest.mark.xfail(
    strict=True,
    reason="canvas_lms.py:235-245 yields student summary missing id/date_iso and catches exceptions with print+return",
)
def test_canvas_lms_fetch_via_handler_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(connector_coverage, "default_path", lambda: tmp_path / "cov" / "cov.json")
    monkeypatch.setattr(canvas_lms, "_build_canvas_config", lambda: ("https://canvas.example.invalid", {"Alex": {"key": "k"}}))
    monkeypatch.setattr(
        canvas_lms,
        "_fetch_student_data",
        lambda url, student, tok: {"student": student, "courses": [], "missing_assignments": []},
    )
    res = fetch_via_handler("canvas_lms", "personal", canvas_lms.fetch, since="7d", auth_context={"tokens": {"Alex": "t"}})
    assert res.error is None and len(res.records) == 1


@pytest.mark.xfail(
    strict=True,
    reason="onenote.py:213-260 yields page dict missing id/date_iso and suppresses exceptions via print+continue",
)
def test_onenote_fetch_via_handler_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(connector_coverage, "default_path", lambda: tmp_path / "cov" / "cov.json")
    monkeypatch.setattr(onenote, "_list_notebooks", lambda tok, include_personal=False: [{"id": "nb1", "displayName": "Home"}])
    monkeypatch.setattr(onenote, "_list_sections", lambda tok, nb_id: [{"id": "s1", "displayName": "Notes"}])
    monkeypatch.setattr(
        onenote,
        "_list_pages",
        lambda tok, sec_id, mod: [{"id": "p1", "title": "Tax Notes", "lastModifiedDateTime": "2026-10-10T00:00:00Z"}],
    )
    monkeypatch.setattr(onenote, "_fetch_page_content", lambda tok, page_id: "<p>synthetic page body</p>")

    res = fetch_via_handler("onenote", "personal", onenote.fetch, since="7d", auth_context={"access_token": "tok"})
    assert res.error is None and len(res.records) == 1


@pytest.mark.xfail(
    strict=True,
    reason="reddit.py:196-206,248-253 emits created_utc instead of date_iso and suppresses _fetch_top exceptions",
)
def test_reddit_fetch_via_handler_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(connector_coverage, "default_path", lambda: tmp_path / "cov" / "cov.json")
    with patch.object(reddit, "_load_blocked_patterns", return_value=[]), patch.object(
        reddit,
        "_fetch_top",
        return_value=[{"name": "t3_1", "title": "Synthetic post", "score": 50, "num_comments": 2, "created_utc": 1760000000}],
    ):
        res = fetch_via_handler(
            "reddit", "personal", reddit.fetch, since="7d",
            auth_context={"subreddits": [{"name": "LocalLLaMA", "tag": "reddit_localllama"}]},
        )
    assert res.error is None and len(res.records) == 1


@pytest.mark.xfail(
    strict=True,
    reason="apple_health.py:107-109 emits offset -0700 without colon, rejected by schemas/connector_record.py:_DATE_RE",
)
def test_apple_health_native_apple_offset_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(connector_coverage, "default_path", lambda: tmp_path / "cov" / "cov.json")
    xml_path = tmp_path / "export.xml"
    recent_start = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d 08:00:00 -0700")
    xml_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><HealthData>'
        '<Record type="HKQuantityTypeIdentifierStepCount" sourceName="Watch" '
        f'unit="count" startDate="{recent_start}" value="4200"/>'
        "</HealthData>",
        encoding="utf-8",
    )
    res = fetch_via_handler("apple_health", "personal", apple_health.fetch, since="30d", auth_context={"file": str(xml_path)})
    assert res.error is None and len(res.records) == 1
