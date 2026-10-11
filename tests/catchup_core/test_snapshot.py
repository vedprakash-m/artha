from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from catchup_core import crypto, ids
from catchup_core import ledger as lg
from catchup_core import snapshot as sn

KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x07" * 32}))


@pytest.fixture
def populated(tmp_path):
    ledger = lg.Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER).open()
    ledger.migrate()
    ledger.put_open_item("OI-1", owner="ved", description="x")
    curated = tmp_path / "lessons.md"
    curated.write_text("lesson one\n")
    yield ledger, curated, tmp_path
    ledger.close()


def test_snapshot_is_consistent_and_verifiable(populated):
    ledger, curated, tmp = populated
    m = sn.take_snapshot(tmp / "ledger.db", tmp / "snaps", generation=1, curated_files={"lessons.md": curated},
                         key_metadata={"current_version": 1}, sealed_checkpoint_id=4,
                         now=datetime(2026, 10, 11, tzinfo=UTC))
    manifest = sn.verify_snapshot(m)
    assert manifest.generation == 1 and manifest.schema_version == 1 and manifest.sealed_checkpoint_id == 4
    assert manifest.key_metadata == {"current_version": 1}
    assert "lessons.md" in manifest.curated and manifest.taken_at == "2026-10-11T00:00:00Z"
    con = sqlite3.connect(tmp / "snaps" / "ledger-1.db")
    assert con.execute("SELECT COUNT(*) FROM open_items").fetchone()[0] == 1
    con.close()
    # no key material in the manifest
    assert "07" * 8 not in m.read_text()


def test_generations_are_immutable_and_validated(populated):
    ledger, curated, tmp = populated
    sn.take_snapshot(tmp / "ledger.db", tmp / "s", generation=1)
    with pytest.raises(sn.SnapshotError):
        sn.take_snapshot(tmp / "ledger.db", tmp / "s", generation=1)
    with pytest.raises(sn.SnapshotError):
        sn.take_snapshot(tmp / "ledger.db", tmp / "s", generation=0)
    with pytest.raises(sn.SnapshotError):
        sn.take_snapshot(tmp / "ledger.db", tmp / "s", generation=2, curated_files={"../evil": curated})


def test_tampered_snapshot_is_refused(populated):
    ledger, curated, tmp = populated
    m = sn.take_snapshot(tmp / "ledger.db", tmp / "s", generation=1, curated_files={"lessons.md": curated})
    (tmp / "s" / "curated-1-lessons.md").write_text("tampered")
    with pytest.raises(sn.SnapshotError, match="curated"):
        sn.verify_snapshot(m)
    (tmp / "s" / "curated-1-lessons.md").write_text("lesson one\n")
    (tmp / "s" / "ledger-1.db").write_bytes(b"garbage")
    with pytest.raises(sn.SnapshotError, match="ledger"):
        sn.verify_snapshot(m)


def test_bad_manifest(tmp_path):
    p = tmp_path / "manifest-1.json"
    p.write_text("{nope")
    with pytest.raises(sn.SnapshotError):
        sn.verify_snapshot(p)
    with pytest.raises(sn.SnapshotError):
        sn.verify_snapshot(tmp_path / "missing.json")


def test_restore_replaces_ledger_and_curated_and_clears_stale_wal(populated):
    ledger, curated, tmp = populated
    m = sn.take_snapshot(tmp / "ledger.db", tmp / "s", generation=1, curated_files={"lessons.md": curated})
    ledger.put_open_item("OI-2", owner="ved", description="later")
    ledger.close()
    (tmp / "ledger.db-wal").write_bytes(b"stale")
    curated.write_text("changed after snapshot\n")
    sn.restore_snapshot(m, tmp / "ledger.db", curated_targets={"lessons.md": curated})
    assert not (tmp / "ledger.db-wal").exists()
    assert curated.read_text() == "lesson one\n"
    restored = lg.Ledger(tmp / "ledger.db", id_key=KEY, cipher=CIPHER).open()
    assert restored.get_open_item("OI-2") is None and restored.get_open_item("OI-1")
    restored.close()


def test_failed_verification_leaves_target_untouched(populated):
    ledger, curated, tmp = populated
    m = sn.take_snapshot(tmp / "ledger.db", tmp / "s", generation=1)
    (tmp / "s" / "ledger-1.db").write_bytes(b"garbage")
    before = (tmp / "ledger.db").read_bytes()
    with pytest.raises(sn.SnapshotError):
        sn.restore_snapshot(m, tmp / "ledger.db")
    assert (tmp / "ledger.db").read_bytes() == before


def test_snapshot_roundtrip_with_old_restore_locks_ledger(tmp_path):
    sealed = lg.MemorySealedStore()
    live = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, sealed=sealed).open()
    live.migrate()
    m = sn.take_snapshot(tmp_path / "l.db", tmp_path / "s", generation=1)
    live.upsert_signal(lg.SignalInput("c", "a", "u1", "v", "t", "H", "blob"))
    live.forget("c", "a", "u1", content_hmac="H")
    live.close()
    sn.restore_snapshot(m, tmp_path / "l.db")
    again = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, sealed=sealed).open()
    assert again.check_restore() is False and again.locked
    again.close()
