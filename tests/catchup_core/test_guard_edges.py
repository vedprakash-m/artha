"""Error paths on the write, restore, scorecard and encrypted-read boundaries."""
from __future__ import annotations

import io
import json
import sys
import types
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from catchup_core import adapters as ad
from catchup_core import backup, cli, commands, crypto, curated, ids, ledger as lg, views
from catchup_core.contracts import Confidentiality
from catchup_core.scorecard import Case, score
from catchup_core.blobs import EncryptedBlobStore
from lib.connector_coverage import report_scope  # noqa: F401  (import path parity with other tests)

KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"7" * 32}))
NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def test_scorecard_explains_every_failure():
    card = score([])
    assert not card.passes and card.failures() == ["no evaluated cases"]
    bad = score([Case("c", frozenset({"a"}), frozenset(), quiet_claim=True, citations_resolve=False,
                      incumbent_surfaced=frozenset({"a"}))])
    text = " ".join(bad.failures())
    assert "false quiet" in text and "unresolved citations" in text and "incumbent" in text and "recall" in text


def test_encrypted_state_is_read_in_memory_through_the_vault(tmp_path, monkeypatch):
    (tmp_path / "goals.md.age").write_bytes(b"cipher")
    seen = []
    fake = types.SimpleNamespace(get_age_vault=lambda: types.SimpleNamespace(read=lambda n: seen.append(n) or "---\ngoals: []\n---\n"))
    monkeypatch.setitem(sys.modules, "foundation", fake)
    assert curated.read_state(tmp_path, "goals").startswith("---") and seen == ["goals"]
    assert curated.records("[1, 2]", "goals") == []


@pytest.fixture
def led(tmp_path):
    ledger = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER).open()
    ledger.migrate()
    yield ledger
    ledger.close()


def test_feedback_refuses_unknown_and_evidence_free_alerts(led):
    with pytest.raises(ValueError, match="unknown Alert"):
        views.feedback(led, "owner", "nope", "useful", "x", NOW)
    led.upsert_alert("k", domain="kids_school", primary_subject_id="kid1", severity="P2")
    with pytest.raises(lg.PermissionDenied):
        views.feedback(led, "owner", "k", "useful", "x", NOW)


def test_acknowledge_marks_the_alert(led):
    fid = led.put_fact(lg.FactInput(domain="kids_school", predicate="test_date", value="2026-10-14",
                                    observed_at="t", primary_subject_id="kid1"))
    led.upsert_alert("k", domain="kids_school", primary_subject_id="kid1", severity="P2", fact_ids=[fid])
    views.feedback(led, "owner", "k", "acknowledge", "seen", NOW)
    assert led.get_alert("k")["status"] == "acknowledged"


def test_export_and_capture_need_the_encryption_key(tmp_path):
    keyless = lg.Ledger(tmp_path / "k.db", id_key=KEY).open()
    keyless.migrate()
    try:
        deps = types.SimpleNamespace(ledger=keyless, viewer="owner", blobs=types.SimpleNamespace(_root=tmp_path),
                                     now=lambda: NOW)
        for name, extra in (("export", {"path": str(tmp_path / "x")}), ("capture", {"text": "t"})):
            with pytest.raises(lg.LedgerError, match="encryption key"):
                commands.execute(Namespace(command=name, **extra), deps, tmp_path)
        fid = keyless.put_fact(lg.FactInput(domain="kids_school", predicate="test_date", value="2026-10-14",
                                            observed_at="t", primary_subject_id="kid1",
                                            confidentiality=Confidentiality.ORDINARY))
        keyless.upsert_alert("k", domain="kids_school", primary_subject_id="kid1", severity="P2", fact_ids=[fid])
        with pytest.raises(ValueError, match="requires encryption"):
            views.feedback(keyless, "owner", "k", "useful", "x", NOW)
    finally:
        keyless.close()


def test_replay_case_requires_encryption(tmp_path):
    keyless = lg.Ledger(tmp_path / "k.db", id_key=KEY).open()
    keyless.migrate()
    try:
        with pytest.raises(ValueError, match="require encryption"):
            views.replay_case(keyless, EncryptedBlobStore(tmp_path / "b", CIPHER, KEY), "owner", "k", "useful", "x",
                              "fbk_1", NOW, tmp_path / "r")
    finally:
        keyless.close()


def test_restore_refuses_a_repository_destination_and_a_mismatched_actor(tmp_path, monkeypatch):
    root = Path(cli.__file__).resolve().parents[2]
    monkeypatch.setattr("catchup_core.runtime.load_runtime_config", lambda _p: types.SimpleNamespace(viewer="owner"))
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["restore", str(tmp_path / "s.ae1"), str(root / "restored")], out=out, err=err) == 20
    assert "outside the repository" in err.getvalue()
    err = io.StringIO()
    assert cli.main(["restore", "--actor", "intruder", str(tmp_path / "s.ae1"), str(tmp_path / "d")], out=out, err=err) == 20
    assert "actor" in err.getvalue()


def test_atomic_bytes_leaves_no_temp_file_when_replace_fails(tmp_path, monkeypatch):
    def refuse(_a, _b):
        raise OSError("cross-device")
    monkeypatch.setattr(backup.os, "replace", refuse)
    with pytest.raises(OSError):
        backup.atomic_bytes(tmp_path / "x.ae1", b"cipher")
    assert list(tmp_path.iterdir()) == []


def test_calendar_attachment_marks_the_record_incomplete():
    raw = {"id": "m1", "source": "gmail", "date_iso": "2026-10-11", "subject": "s", "body": "b",
           "attachments": [{"name": "form.pdf"}]}
    got = ad.fetch_via_handler("school_email", "acct", lambda **_: iter([raw]), since="2026-10-01")
    assert got.records[0].incomplete and any("attachment" in b for b in got.blind_spots)


def test_default_cutover_flag_follows_environment_and_authoritative_runs_stay_disabled(monkeypatch):
    from catchup_core.contracts import CatchupRequest, ExecutionMode
    monkeypatch.setenv("ARTHA_CUTOVER", "0")
    with pytest.raises(cli.NotConfigured, match="disabled until the pilot is cut over"):
        cli._default_factory(CatchupRequest("cu_x", "owner", "cli", mode=ExecutionMode.AUTHORITATIVE, host="h"))
