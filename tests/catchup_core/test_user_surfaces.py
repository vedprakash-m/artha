from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from catchup_core.backup import encrypted_snapshot, read_snapshot, restore_encrypted_snapshot
from catchup_core import crypto, ids, ledger as lg, views
from catchup_core.curated import records
from catchup_core.session_lock import SessionBusy, session_lock
from catchup_core.blobs import EncryptedBlobStore

KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"7" * 32}))


@pytest.fixture
def store(tmp_path):
    led = lg.Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER,
                    consent_subjects=frozenset({"adult"})).open()
    led.migrate()
    yield led
    led.close()


def fact(store, **kw):
    return store.put_fact(lg.FactInput(domain="kids_school", predicate="test_date", value="2026-10-14",
        observed_at="2026-10-12T00:00:00Z", primary_subject_id="kid1", **kw))


def test_beliefs_filter_before_rendering(store):
    visible = fact(store)
    private = store.put_fact(lg.FactInput(domain="health", predicate="status", value="private",
        observed_at="2026-10-12T00:00:00Z", primary_subject_id="adult",
        confidentiality=lg.Confidentiality.RESTRICTED))
    assert [f["fact_id"] for f in views.beliefs(store, "owner")] == [visible]
    assert [f["fact_id"] for f in views.beliefs(store, "adult")] == [private]
    assert views.beliefs(store, "owner", domain="finance") == []


def test_co_subject_requires_strictest_permission(store):
    fid = fact(store, co_subjects=(("adult", "co_subject"),))
    with pytest.raises(lg.PermissionDenied):
        store.get_fact(fid, "owner")
    grant = store.grant_consent("adult", "adult", "kids_school", ["view"], "owner")
    assert store.get_fact(fid, "owner")
    store.revoke_consent(grant)
    with pytest.raises(lg.PermissionDenied):
        store.get_fact(fid, "owner")


def test_correction_requires_reviewed_version(store):
    fid = fact(store)
    old = store.get_fact(fid, "owner")
    views.correct(store, "owner", fid, "2026-10-16", old.recorded_at)
    updated = store.get_fact(fid, "owner")
    assert updated.provenance_class == "user_confirmed" and updated.value == "2026-10-16"
    with pytest.raises(ValueError, match="changed"):
        views.correct(store, "owner", fid, "2026-10-17", old.recorded_at)


def test_legacy_sensitive_values_are_reencrypted_without_rewriting_history(store):
    fid = fact(store, confidentiality=lg.Confidentiality.SENSITIVE)
    old = store.get_fact(fid, "owner")
    payload = json.dumps("SYNTHETIC-LEGACY-SECRET")
    store.conn.execute("UPDATE facts SET typed_value=?,value_encrypted=0,key_version=NULL WHERE fact_id=?", (payload, fid))
    store.conn.execute("UPDATE fact_versions SET typed_value=?,value_encrypted=0,key_version=NULL WHERE fact_id=?",
                       (payload, fid))
    assert store.protect_legacy_values() == 2
    assert store.protect_legacy_values() == 0
    current = store.get_fact(fid, "owner")
    assert current.value == "SYNTHETIC-LEGACY-SECRET" and current.recorded_at == old.recorded_at
    assert b"SYNTHETIC-LEGACY-SECRET" not in store._path.read_bytes()


def test_consent_expiry_compares_instants_across_offsets(tmp_path):
    with lg.Ledger(tmp_path / "expiry.db", id_key=KEY, cipher=CIPHER,
                   clock=lambda: "2026-10-12T23:00:00Z").open() as led:
        led.migrate()
        led.grant_consent("adult", "adult", "health", ["view"], "owner",
                          expires_at="2026-10-13T01:00:00+14:00")
        assert not led.consent_active("adult", "view", "owner", scope="health")
        with pytest.raises(ValueError, match="offset"):
            led.grant_consent("adult", "adult", "health", ["view"], "owner", expires_at="2026-10-14")


def test_terminal_projection_is_readable_and_inert():
    text = views.render("beliefs", {"beliefs": [{"fact_id": "f1", "predicate": "<b>date</b>",
                       "value": "2026-10-14", "status": "unknown"}]})
    assert "f1: date" in text and "<b>" not in text
    assert views.render("beliefs", {"beliefs": []}) == "No beliefs recorded here."
    assert "deleted signals: 1" in views.render("forget", {"deleted_signals": 1})


def test_feedback_is_reviewed_not_automatically_gold(store):
    fid = fact(store)
    store.upsert_alert("exam", domain="kids_school", primary_subject_id="kid1", severity="P1", fact_ids=[fid])
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    views.feedback(store, "owner", "exam", "snooze", "already planned", now)
    assert store.get_alert("exam")["status"] == "snoozed"
    assert views.precision(store)[0]["review_status"] == "unreviewed"
    with pytest.raises(ValueError):
        views.feedback(store, "owner", "exam", "dismiss", "", now)


def test_open_items_are_read_without_losing_active_commitments():
    text = "## Active\n- id: OI-001\n  description: Register for exam\n  status: open\n\n" \
           "- id: OI-002\n  description: Resolved\n  status: done\n"
    assert [r["id"] for r in records(text, "open_items")] == ["OI-001"]
    assert records('---\ngoals:\n  - id: G-1\n    owner: owner\n---\n', "goals")[0]["id"] == "G-1"


def test_separate_process_cannot_take_a_live_session_lock(tmp_path):
    lock = tmp_path / "session.lock"
    module = Path(__file__).resolve().parents[2] / "scripts"
    script = ("import sys; sys.path.insert(0,sys.argv[1]); "
              "from catchup_core.session_lock import session_lock; from pathlib import Path; "
              "lock=session_lock(Path(sys.argv[2])); lock.__enter__()")
    with session_lock(lock):
        result = subprocess.run([sys.executable, "-c", script, str(module), str(lock)], capture_output=True)
    assert result.returncode != 0 and b"another Catch-up" in result.stderr
    with session_lock(lock):
        pass  # no stale marker can prevent safe recovery once the process exits


class Keyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value


def test_encrypted_export_restore_replays_new_forgets_and_revocations(tmp_path):
    sealed = lg.KeyringSealedStore(Keyring(), "test")
    led = lg.Ledger(tmp_path / "original.db", id_key=KEY, cipher=CIPHER, sealed=sealed).open()
    led.migrate()
    led.seal_privacy()
    blobs = EncryptedBlobStore(tmp_path / "blobs", CIPHER, KEY)
    ref = blobs.put("m1", "SYNTHETIC-PRIVATE-DOCUMENT")
    sid, _ = led.upsert_signal(lg.SignalInput("mail", "a", "m1", "v1", "t", "h1", ref))
    led.put_fact(lg.FactInput(domain="health", predicate="value", value="SYNTHETIC-SECRET",
        primary_subject_id="adult", observed_at="t", evidence=((sid, 0, 10),)))
    grant = led.grant_consent("adult", "adult", "health", ["view"], "owner")
    export = tmp_path / "snapshot.ae1"
    encrypted_snapshot(export, tmp_path / "original.db", CIPHER, blobs=tmp_path / "blobs")
    assert b"SYNTHETIC" not in export.read_bytes()
    assert "ledger.db" in read_snapshot(export, CIPHER)
    led.revoke_consent(grant)
    led.forget("mail", "a", "m1")
    destination = tmp_path / "restored"
    restore_encrypted_snapshot(export, destination, CIPHER, sealed)
    led.close()
    restored = lg.Ledger(destination / "ledger.db", id_key=KEY, cipher=CIPHER, sealed=sealed).open()
    assert restored.check_restore()
    assert restored.conn.execute("SELECT count(*) FROM signals").fetchone()[0] == 0
    assert not restored.consent_active("adult", "view", "owner", scope="health")
    assert not (destination / "blobs" / f"{ref}.blob").exists()
    restored.close()
    with pytest.raises(lg.LedgerError):
        restore_encrypted_snapshot(export, destination, CIPHER, sealed)


def test_feedback_becomes_an_encrypted_unreviewed_replay_case_that_forget_deletes(store, tmp_path):
    blobs = EncryptedBlobStore(tmp_path / "blobs", CIPHER, KEY)
    store._blob_deleter = blobs.delete
    text = "Registration closes 2026-10-14 for kid1."
    ref = blobs.put("gmail|a|m1|v1", text)
    chmac = ids.content_hmac(KEY, text)
    sid, _ = store.upsert_signal(lg.SignalInput("gmail", "a", "m1", "v1", "2026-10-12T00:00:00Z", chmac, ref))
    fid = store.put_fact(lg.FactInput(domain="kids_school", predicate="test_date", value="2026-10-14",
        observed_at="2026-10-12T00:00:00Z", primary_subject_id="kid1", evidence=((sid, 20, 30),)))
    store.upsert_alert("exam", domain="kids_school", primary_subject_id="kid1", severity="P1", fact_ids=[fid])
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    replay_dir = tmp_path / "replay"
    store._replay_deleter = lambda ids_: [(replay_dir / f"{i}.ae1").unlink(missing_ok=True) for i in ids_]
    feedback_id = views.feedback(store, "owner", "exam", "dismiss", "already handled", now)
    path = views.replay_case(store, blobs, "owner", "exam", "dismiss", "already handled", feedback_id, now, replay_dir)
    assert text.encode() not in path.read_bytes()                        # ciphertext only
    bundle = json.loads(CIPHER.decrypt(path.read_bytes(), aad=f"replay:{feedback_id}".encode()))
    assert bundle["expected_outcome"] == "pending_review" and bundle["inputs"][0]["text"] == text
    store.forget("gmail", "a", "m1")
    assert not path.exists()
    assert store.conn.execute("SELECT count(*) FROM replay_cases").fetchone()[0] == 0


def test_replay_case_for_unknown_alert_is_refused(store, tmp_path):
    blobs = EncryptedBlobStore(tmp_path / "blobs", CIPHER, KEY)
    with pytest.raises(ValueError):
        views.replay_case(store, blobs, "owner", "nope", "dismiss", "x", "fbk_1",
                          datetime(2026, 10, 12, tzinfo=timezone.utc), tmp_path / "replay")
