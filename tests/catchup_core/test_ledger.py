from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from catchup_core import crypto, ids
from catchup_core import ledger as lg
from catchup_core.contracts import (
    Confidentiality, FactStatus, FailureClass, ProcessingState, Provenance, Stage, StageCheckpoint,
)

KEY = ids.HmacKey(1, b"i" * 32)
LATEST = lg.Ledger.available_migrations()[-1][0]
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x07" * 32}))


@pytest.fixture
def clock():
    state = {"n": 0}

    def tick() -> str:
        state["n"] += 1
        return f"2026-10-11T00:00:{state['n']:02d}Z"
    return tick


@pytest.fixture
def led(tmp_path, clock):
    ledger = lg.Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER, owner_id="ved", consent_subjects=frozenset({"adult_child"}),
                       clock=clock).open()
    ledger.migrate()
    yield ledger
    ledger.close()


def sig(upstream="m1", version="v1", conf=Confidentiality.SENSITIVE, hmac="h1", scope="acct"):
    return lg.SignalInput("school_email", scope, upstream, version, "2026-10-11T00:00:00Z", hmac,
                          f"blob:{upstream}:{version}", conf)


def fact(**kw):
    base = dict(domain="kids_school", predicate="exam_registration_deadline", value={"date": "2026-10-19"},
                primary_subject_id="kid1", observed_at="2026-10-11T00:00:00Z",
                confidentiality=Confidentiality.ORDINARY)
    base.update(kw)
    return lg.FactInput(**base)


# ── connection + migrations ───────────────────────────────────────────────

def test_connection_pragmas(led):
    c = led.conn
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert led.schema_version() == LATEST
    assert led.integrity_check()


def test_migration_idempotent(led):
    assert led.migrate() == LATEST


def test_existing_database_refuses_migration_without_verified_backup(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE legacy(x)")
    con.commit()
    con.close()
    ledger = lg.Ledger(path, id_key=KEY).open()
    try:
        with pytest.raises(lg.MigrationRefused):
            ledger.migrate()
        with pytest.raises(lg.MigrationRefused):
            ledger.migrate(backup=lambda p: (_ for _ in ()).throw(OSError("disk full")))
        empty = tmp_path / "empty.bak"
        empty.write_bytes(b"")
        with pytest.raises(lg.MigrationRefused):
            ledger.migrate(backup=lambda p: empty)
        assert ledger.schema_version() == 0
        good = tmp_path / "ok.bak"
        good.write_bytes(b"data")
        assert ledger.migrate(backup=lambda p: good) == LATEST
    finally:
        ledger.close()


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    ledger = lg.Ledger(tmp_path / "x.db", id_key=KEY).open()
    bad = tmp_path / "0002_bad.sql"
    bad.write_text("CREATE TABLE ok_table(x);\nCREATE TABLE ok_table(x);")
    monkeypatch.setattr(lg.Ledger, "available_migrations",
                        staticmethod(lambda: [(1, lg._MIGRATIONS_DIR / "0001_init.sql"), (2, bad)]))
    with pytest.raises(sqlite3.OperationalError):
        ledger.migrate()
    assert ledger.schema_version() == 1
    assert not ledger.conn.execute("SELECT 1 FROM sqlite_master WHERE name='ok_table'").fetchone()
    ledger.close()


def test_not_open_raises(tmp_path):
    with pytest.raises(lg.LedgerError):
        lg.Ledger(tmp_path / "n.db", id_key=KEY).conn


def test_context_manager_opens_and_closes(tmp_path):
    with lg.Ledger(tmp_path / "c.db", id_key=KEY) as ledger:
        ledger.migrate()
        assert ledger.schema_version() == LATEST
    with pytest.raises(lg.LedgerError):
        ledger.conn


# ── signals ───────────────────────────────────────────────────────────────

def test_signal_replay_does_not_duplicate_and_new_version_does(led):
    sid, created = led.upsert_signal(sig())
    assert created and sid.startswith("sig_")
    assert led.upsert_signal(sig()) == (sid, False)
    sid2, created2 = led.upsert_signal(sig(version="v2"))
    assert created2 and sid2 != sid
    assert led.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 2


def test_processing_state_transitions_and_closure(led):
    a, _ = led.upsert_signal(sig("a"))
    b, _ = led.upsert_signal(sig("b", hmac="h2"))
    assert led.unresolved_signals() == sorted([a, b])
    led.set_processing_state(a, ProcessingState.RECONCILED)
    led.set_processing_state(b, ProcessingState.EXCLUDED, "newsletter")
    assert led.unresolved_signals() == []
    assert led.signal_state(a) == "reconciled" and led.signal_state("sig_none") is None
    with pytest.raises(lg.LedgerError):
        led.set_processing_state(a, ProcessingState.EXCLUDED)
    with pytest.raises(lg.LedgerError):
        led.set_processing_state("sig_missing", ProcessingState.DECODED)


# ── facts ─────────────────────────────────────────────────────────────────

def test_ordinary_fact_roundtrip_with_evidence_and_viewers(led):
    sid, _ = led.upsert_signal(sig())
    fid = led.put_fact(fact(evidence=((sid, 0, 10),), viewers=("spouse",), co_subjects=(("kid2", "sibling"),)))
    view = led.get_fact(fid, "ved")
    assert view.value == {"date": "2026-10-19"} and view.evidence == ((sid, 0, 10),)
    assert led.get_fact(fid, "spouse").fact_id == fid
    with pytest.raises(lg.PermissionDenied):
        led.get_fact(fid, "stranger")


def test_restricted_fact_is_stored_only_as_ciphertext(led):
    fid = led.put_fact(fact(predicate="accommodation_status", value="approved",
                            confidentiality=Confidentiality.RESTRICTED))
    raw = led.conn.execute("SELECT typed_value, value_encrypted, key_version FROM facts WHERE fact_id=?",
                           (fid,)).fetchone()
    assert raw["value_encrypted"] == 1 and raw["key_version"] == 1
    assert "approved" not in raw["typed_value"]
    assert led.get_fact(fid, "ved").value == "approved"
    hist = led.conn.execute("SELECT typed_value FROM fact_versions WHERE fact_id=?", (fid,)).fetchone()[0]
    assert "approved" not in hist


def test_restricted_value_without_key_refuses_plaintext(tmp_path, clock):
    ledger = lg.Ledger(tmp_path / "nk.db", id_key=KEY, cipher=None, clock=clock).open()
    ledger.migrate()
    with pytest.raises(lg.LedgerError):
        ledger.put_fact(fact(confidentiality=Confidentiality.RESTRICTED))
    assert ledger.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
    ledger.close()


def test_check_constraint_blocks_restricted_plaintext_even_via_sql(led):
    with pytest.raises(sqlite3.IntegrityError):
        led.conn.execute(
            "INSERT INTO facts VALUES('f','d','p','{}',0,NULL,'s','restricted',NULL,NULL,0,NULL,'t','t',"
            "'verified_source','current',NULL)")


def test_fact_versions_are_append_only_history(led):
    fid = led.put_fact(fact(value={"date": "2026-10-19"}))
    led.put_fact(fact(value={"date": "2026-10-22"}), fact_id=fid)
    hist = led.fact_history(fid, "ved")
    assert [h["value"]["date"] for h in hist] == ["2026-10-19", "2026-10-22"]
    # what Artha knew at the time of the first recording
    first_time = hist[0]["recorded_at"]
    as_of = led.fact_history(fid, "ved", as_of=first_time)
    assert [h["value"]["date"] for h in as_of] == ["2026-10-19"]
    assert led.get_fact(fid, "ved").value == {"date": "2026-10-22"}


def test_update_unknown_fact_and_unknown_get(led):
    with pytest.raises(lg.LedgerError):
        led.put_fact(fact(), fact_id="fct_missing")
    with pytest.raises(lg.LedgerError):
        led.get_fact("fct_missing", "ved")


def test_validity_check_constraint(led):
    with pytest.raises(sqlite3.IntegrityError):
        led.put_fact(fact(valid_from="2026-10-20", valid_to="2026-10-01"))


def test_facts_for_filters_status_and_permission(led):
    a = led.put_fact(fact(viewers=("spouse",)))
    b = led.put_fact(fact(value={"date": "x"}, status=FactStatus.SUPERSEDED))
    led.put_fact(fact(value={"date": "y"}))     # owner-only
    got = led.facts_for("kids_school", "exam_registration_deadline", "spouse")
    assert [g.fact_id for g in got] == [a]
    both = led.facts_for("kids_school", "exam_registration_deadline", "ved",
                         statuses=(FactStatus.CURRENT, FactStatus.SUPERSEDED))
    assert b in [g.fact_id for g in both]


# ── consent ───────────────────────────────────────────────────────────────

def test_restricted_data_about_another_person_needs_consent(led):
    fid = led.put_fact(fact(predicate="accommodation_status", value="approved", primary_subject_id="adult_child",
                            confidentiality=Confidentiality.RESTRICTED, viewers=("spouse",)))
    with pytest.raises(lg.PermissionDenied):
        led.get_fact(fid, "spouse")
    with pytest.raises(lg.PermissionDenied):
        led.get_fact(fid, "ved")                      # even the owner
    cid = led.grant_consent("adult_child", "adult_child", "kids_school", ["view"], audience="*")
    assert led.get_fact(fid, "ved").value == "approved"
    led.revoke_consent(cid)
    with pytest.raises(lg.PermissionDenied):
        led.get_fact(fid, "ved")
    assert led.get_fact(fid, "adult_child").value == "approved"        # the subject always can


def test_consent_rules(led):
    with pytest.raises(lg.PermissionDenied):
        led.grant_consent("kid", "stranger", "s", ["view"], "*")
    cid = led.grant_consent("kid", "ved", "s", ["view"], "spouse", expires_at="2026-10-11T00:00:00Z")
    assert not led.consent_active("kid", "view", "spouse")             # already expired by the test clock
    cid2 = led.grant_consent("kid", "kid", "s", ["view"], "spouse")
    assert led.consent_active("kid", "view", "spouse")
    assert not led.consent_active("kid", "edit", "spouse")
    assert not led.consent_active("kid", "view", "someone_else")
    led.revoke_consent(cid2)
    with pytest.raises(lg.LedgerError):
        led.revoke_consent(cid2)
    assert cid != cid2


# ── alerts ────────────────────────────────────────────────────────────────

def test_one_alert_per_obligation_updated_not_duplicated(led):
    fid = led.put_fact(fact())
    a1, created = led.upsert_alert("kid1:exam:2026", domain="kids_school", primary_subject_id="kid1",
                                   severity="P2", deadline="2026-10-19", fact_ids=[fid], viewers=["spouse"])
    a2, created2 = led.upsert_alert("kid1:exam:2026", domain="kids_school", primary_subject_id="kid1",
                                    severity="P1", deadline="2026-10-19")
    assert created and not created2 and a1 == a2
    row = led.get_alert("kid1:exam:2026")
    assert row["severity"] == "P1" and row["last_seen"] > row["first_seen"]
    led.resolve_alert("kid1:exam:2026", "receipt:123")
    assert led.get_alert("kid1:exam:2026")["status"] == "resolved"
    with pytest.raises(lg.LedgerError):
        led.resolve_alert("nope", "x")
    assert led.get_alert("nope") is None


# ── open items ────────────────────────────────────────────────────────────

def test_open_item_description_is_encrypted(led):
    led.put_open_item("OI-001", owner="ved", description="Register Child1 for the SAT", deadline="2026-10-19")
    raw = led.conn.execute("SELECT description_encrypted FROM open_items").fetchone()[0]
    assert "SAT" not in raw
    item = led.get_open_item("OI-001")
    assert item["description"] == "Register Child1 for the SAT" and item["state"] == "open"
    led.put_open_item("OI-001", owner="ved", description="done now", state="done")
    assert led.get_open_item("OI-001")["state"] == "done"
    assert led.get_open_item("OI-404") is None


def test_open_items_need_a_key(tmp_path, clock):
    ledger = lg.Ledger(tmp_path / "nk.db", id_key=KEY, clock=clock).open()
    ledger.migrate()
    with pytest.raises(lg.LedgerError):
        ledger.put_open_item("OI-1", owner="x", description="d")
    ledger.conn.execute("INSERT INTO open_items VALUES('OI-2','x',NULL,'open','blob',1,NULL,NULL,'t','t',NULL)")
    with pytest.raises(lg.LedgerError):
        ledger.get_open_item("OI-2")
    ledger.close()


# ── forgetting ────────────────────────────────────────────────────────────

def _forget_fixture(led):
    sid, _ = led.upsert_signal(sig("m9", hmac="HM9"))
    fid = led.put_fact(fact(evidence=((sid, 0, 5),)))
    other_sid, _ = led.upsert_signal(sig("m10", hmac="HM10"))
    other = led.put_fact(fact(value={"date": "2026-11-01"}, evidence=((other_sid, 0, 5),)))
    led.upsert_alert("ob:1", domain="d", primary_subject_id="kid1", severity="P2", fact_ids=[fid])
    led.upsert_alert("ob:2", domain="d", primary_subject_id="kid1", severity="P2", fact_ids=[other])
    return sid, fid, other_sid, other


def test_forget_removes_signal_facts_alerts_and_blocks_refetch(tmp_path, clock):
    deleted_blobs: list[str] = []
    ledger = lg.Ledger(tmp_path / "f.db", id_key=KEY, cipher=CIPHER, clock=clock, owner_id="ved",
                       blob_deleter=deleted_blobs.append).open()
    ledger.migrate()
    sid, fid, other_sid, other = _forget_fixture(ledger)
    receipt = ledger.forget("school_email", "acct", "m9", content_hmac="HM9", reason="user request")
    assert (receipt.deleted_signals, receipt.deleted_facts, receipt.deleted_alerts) == (1, 1, 1)
    assert deleted_blobs == ["blob:m9:v1"] and receipt.backup_copies_pending_expiry
    assert ledger.signal_state(sid) is None
    assert ledger.get_alert("ob:1") is None and ledger.get_alert("ob:2") is not None
    with pytest.raises(lg.LedgerError):
        ledger.get_fact(fid, "ved")
    assert ledger.get_fact(other, "ved")
    with pytest.raises(lg.Suppressed):                       # re-fetch of the same upstream id
        ledger.upsert_signal(sig("m9", version="v2", hmac="NEW"))
    with pytest.raises(lg.Suppressed):                       # same content under another id
        ledger.upsert_signal(sig("m77", hmac="HM9"))
    assert ledger.integrity_check()
    assert ledger.conn.execute("SELECT state FROM deletion_journal").fetchone()[0] == "receipted"
    ledger.close()


def test_forget_by_upstream_id_without_hmac(led):
    led.upsert_signal(sig("only"))
    r = led.forget("school_email", "acct", "only")
    assert r.deleted_signals == 1
    with pytest.raises(lg.Suppressed):
        led.upsert_signal(sig("only", version="v9"))


def test_interrupted_forget_resumes_from_journal(tmp_path, clock):
    ledger = lg.Ledger(tmp_path / "r.db", id_key=KEY, cipher=CIPHER, clock=clock).open()
    ledger.migrate()
    sid, fid, *_ = _forget_fixture(ledger)
    # Simulate a crash after journal+tombstone commit but before sealing/deletion.
    ledger.conn.execute("INSERT INTO tombstones(tombstone_id,connector,account_scope,upstream_id,content_hmac,"
                        "created_at) VALUES('tmb_x','school_email','acct','m9','HM9','t')")
    ledger.conn.execute("INSERT INTO deletion_journal(tombstone_id,requested_at,state) VALUES('tmb_x','t','requested')")
    assert ledger.signal_state(sid) is not None
    receipts = ledger.resume_pending_forgets()
    assert len(receipts) == 1 and receipts[0].deleted_signals == 1
    assert ledger.signal_state(sid) is None
    assert ledger.resume_pending_forgets() == []
    ledger.close()


def test_unknown_tombstone_on_resume(led):
    with pytest.raises(lg.LedgerError):
        led._complete_forget(1, "tmb_nope")


# ── restore guard / sealed checkpoint ─────────────────────────────────────

def test_old_restore_is_locked_until_tombstones_are_replayed(tmp_path, clock):
    sealed = lg.MemorySealedStore()
    live = lg.Ledger(tmp_path / "live.db", id_key=KEY, cipher=CIPHER, sealed=sealed, clock=clock).open()
    live.migrate()
    live.upsert_signal(sig("keep", hmac="K"))
    old = tmp_path / "snapshot.db"
    live.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    import shutil
    shutil.copy(tmp_path / "live.db", old)                       # a backup taken before the forget
    live.upsert_signal(sig("gone", hmac="G"))
    live.forget("school_email", "acct", "gone", content_hmac="G")
    entries = live.tombstones()
    live.close()

    restored = lg.Ledger(old, id_key=KEY, cipher=CIPHER, sealed=sealed, clock=clock).open()
    assert restored.check_restore() is False and restored.locked
    with pytest.raises(lg.LedgerLocked):
        restored.upsert_signal(sig("x", hmac="X"))
    with pytest.raises(lg.LedgerLocked):
        restored.put_fact(fact())
    assert restored.replay_tombstones(entries) is True
    assert not restored.locked and restored.check_restore()
    with pytest.raises(lg.Suppressed):
        restored.upsert_signal(sig("gone", version="v3", hmac="G"))
    restored.close()


def test_replay_with_missing_history_stays_locked(tmp_path, clock):
    sealed = lg.MemorySealedStore()
    live = lg.Ledger(tmp_path / "a.db", id_key=KEY, cipher=CIPHER, sealed=sealed, clock=clock).open()
    live.migrate()
    live.upsert_signal(sig("s1", hmac="S1"))
    live.forget("school_email", "acct", "s1", content_hmac="S1")
    live.close()
    fresh = lg.Ledger(tmp_path / "b.db", id_key=KEY, cipher=CIPHER, sealed=sealed, clock=clock).open()
    fresh.migrate()
    assert fresh.check_restore() is False
    assert fresh.replay_tombstones([]) is False and fresh.locked    # unverifiable history fails safe
    fresh.close()


def test_file_sealed_store_roundtrip_and_corruption(tmp_path):
    store = lg.FileSealedStore(tmp_path / "sealed" / "cp.json")
    assert store.read() is None
    cp = lg.SealedCheckpoint(3, "abc", 2)
    store.write(cp)
    assert store.read() == cp
    (tmp_path / "sealed" / "cp.json").write_text("{broken")
    with pytest.raises(lg.LedgerLocked):
        store.read()


# ── stage journal ─────────────────────────────────────────────────────────

def test_stage_journal_records_and_updates(led):
    led.record_stage(StageCheckpoint("cu1", Stage.FETCH, "committed", {"policy": "1"}, ("a1",)))
    led.record_stage(StageCheckpoint("cu1", Stage.NORMALIZE, "failed", failure_class=FailureClass.DECODE))
    assert led.committed_stages("cu1") == ["FETCH"]
    led.record_stage(StageCheckpoint("cu1", Stage.NORMALIZE, "committed", {}, ("a2",)))
    assert sorted(led.committed_stages("cu1")) == ["FETCH", "NORMALIZE"]
    assert led.committed_stages("other") == []


def test_unique_provenance_values_cover_enum(led):
    for prov in Provenance:
        fid = led.put_fact(fact(provenance=prov))
        assert led.get_fact(fid, "ved").provenance_class == prov.value
