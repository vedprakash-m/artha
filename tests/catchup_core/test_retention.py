"""Synthetic retention checks; never open an installation's private store."""
import hashlib
import os
import sqlite3
import sys
from contextlib import closing, contextmanager, nullcontext
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from catchup_core import crypto, ids
from catchup_core.blobs import EncryptedBlobStore, private_atomic_bytes
from catchup_core.contracts import Confidentiality, ProcessingState
from catchup_core.ledger import FactInput, Ledger, SignalInput, Suppressed
from catchup_core.retention import RetentionError, RetentionPlanner, RetentionPolicy


@pytest.mark.parametrize("raw", [None, [], {"unknown": 30}, {"source": True},
                                     {"source": 0}, {"source": -1}, {"source": "30"},
                                     {"source": 1.5}, {"source": 3650001}])
def test_policy_rejects_unknown_classes_and_nonliteral_days(raw):
    with pytest.raises(RetentionError, match="policy"):
        RetentionPolicy.from_mapping(raw)


def test_policy_has_no_automatic_periods():
    policy = RetentionPolicy.from_mapping({"replay": 30})
    assert policy.days_for("replay") == 30
    assert policy.days_for("source") is None
    assert RetentionPolicy.from_mapping({}).days_for("snapshot") is None


def test_policy_is_immutable_and_canonical():
    raw = {"source": 10, "replay": 20}
    policy = RetentionPolicy.from_mapping(raw)
    raw["source"] = 1
    assert policy.days_for("source") == 10
    assert policy == RetentionPolicy.from_mapping({"replay": 20, "source": 10})


NOW = datetime(2026, 10, 24, tzinfo=UTC)
OLD = "2020-01-01T00:00:00+00:00"


@pytest.fixture
def store(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    migrations = [(number, path) for number, path in Ledger.available_migrations() if number <= 16]
    monkeypatch.setattr(Ledger, "available_migrations", staticmethod(lambda: migrations))
    key = ids.HmacKey(1, b"r" * 32)
    cipher = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"s" * 32}))
    blobs = EncryptedBlobStore(tmp_path / "blobs", cipher, key)
    ledger = Ledger(tmp_path / "ledger.db", id_key=key, cipher=cipher, owner_id="owner",
                    consent_subjects=frozenset({"adult"}), known_subjects=frozenset({"owner", "adult"}),
                    clock=lambda: NOW.isoformat(), blob_deleter=blobs.delete).open()
    ledger.migrate()
    yield ledger, blobs, tmp_path
    ledger.close()


def signal(store, upstream="private-message", subject="owner", connector="mail"):
    ledger, blobs, _root = store
    ref = blobs.put(upstream, "synthetic private evidence")
    sid, _ = ledger.upsert_signal(SignalInput(connector, "account", upstream, "v1", OLD,
                                             ids.content_hmac(ledger._id_key, upstream), ref,
                                             Confidentiality.SENSITIVE))
    ledger.set_signal_subjects(sid, (subject,))
    ledger.set_processing_state(sid, ProcessingState.RECONCILED)
    return sid, ref


def replay(store, sid, identity="fbk_1234"):
    ledger, _blobs, root = store
    ledger.conn.execute("INSERT INTO feedback(feedback_id,obligation_key,response,reason,actor,recorded_at) "
                        "VALUES(?,?,'useful','encrypted','owner',?)", (identity, "test-obligation", OLD))
    ledger.conn.execute("INSERT INTO replay_cases VALUES(?,?)", (identity, sid))
    directory = root / "replay"
    directory.mkdir(exist_ok=True)
    assert ledger._cipher is not None
    path = directory / f"{identity}.ae1"
    private_atomic_bytes(path, ledger._cipher.encrypt(b"synthetic replay", aad=f"replay:{identity}".encode())[0])

    def erase(feedback_ids):
        for feedback_id in feedback_ids:
            (directory / f"{feedback_id}.ae1").unlink(missing_ok=True)

    ledger._replay_deleter = erase
    return path


def planner(store, policy=None, **kwargs):
    ledger, _blobs, root = store
    return RetentionPlanner(ledger, RetentionPolicy.from_mapping(policy or {}), "owner", NOW, root, **kwargs)


def decisions(preview, artifact_class):
    return [entry for entry in preview.entries if entry.artifact_class == artifact_class]


@pytest.mark.parametrize("mode", ["read_only", "query_only"])
def test_preview_uses_only_a_read_transaction(store, mode):
    ledger, blobs, root = store
    signal(store)
    if mode == "read_only":
        ledger = Ledger(ledger._path, id_key=ledger._id_key, cipher=ledger._cipher,
                        sealed=ledger._sealed, owner_id="owner").open(read_only=True)
    else:
        ledger.conn.execute("PRAGMA query_only=ON")
    statements = []
    ledger.conn.set_trace_callback(statements.append)
    before = ledger.conn.total_changes
    try:
        preview = planner((ledger, blobs, root), {"source": 1}).preview()
        assert decisions(preview, "source")[0].status == "expired"
        assert ledger.conn.total_changes == before
        assert not ledger.conn.in_transaction
        assert "BEGIN" in statements and "ROLLBACK" in statements
        assert not any("IMMEDIATE" in statement or "SAVEPOINT" in statement for statement in statements)
    finally:
        ledger.conn.set_trace_callback(None)
        if mode == "read_only":
            ledger.close()
        else:
            ledger.conn.execute("PRAGMA query_only=OFF")


@pytest.mark.parametrize("invalid", [False, True])
def test_preview_preserves_caller_transaction_without_savepoints(store, invalid):
    ledger, _blobs, _root = store
    sid, _ref = signal(store)
    ledger.conn.execute("BEGIN")
    ledger.conn.execute("UPDATE signals SET processing_state='discovered' WHERE signal_id=?", (sid,))
    statements = []
    ledger.conn.set_trace_callback(statements.append)
    try:
        review = planner(store, {"source": 1}, max_rows=1 if invalid else 10000)
        if invalid:
            with pytest.raises(RetentionError, match="bound"):
                review.preview()
        else:
            assert decisions(review.preview(), "source")[0].status == "protected"
        assert ledger.conn.in_transaction
        assert not any(statement.split()[0] in ("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE")
                       for statement in statements)
    finally:
        ledger.conn.set_trace_callback(None)
        ledger.conn.execute("ROLLBACK")
    assert ledger.signal_row(sid)["processing_state"] == "reconciled"


def expect_applied(review, preview):
    result = review.apply(preview, preview.digest, mutation_guard=nullcontext)
    assert not result.interrupted
    return result


def test_approved_source_expiry_uses_the_deletion_journal_with_a_content_keyed_tombstone(store):
    ledger, _blobs, root = store
    sid, ref = signal(store)
    marker = ledger.signal_row(sid)["content_hmac"]
    review = planner(store, {"source": 1})
    preview = review.preview()
    entry = decisions(preview, "source")[0]
    assert (entry.status, entry.reason) == ("expired", "explicit_period_elapsed")
    assert dict(preview.coverage)["source"] == "applicable"
    assert expect_applied(review, preview).applied == 1
    assert ledger.signal_row(sid) is None and not (root / "blobs" / f"{ref}.blob").exists()
    [tombstone] = ledger.tombstones()
    assert (tombstone["reason"], tombstone["connector"], tombstone["upstream_id"]) == ("retention:source", None, None)
    assert tombstone["content_hmac"] == marker
    assert ledger.conn.execute("SELECT state FROM deletion_journal").fetchall()[0][0] == "receipted"
    assert decisions(review.preview(), "source") == []


def test_expired_content_cannot_return_but_a_changed_version_stays_admissible(store):
    ledger, blobs, _root = store
    signal(store)
    marker = ids.content_hmac(ledger._id_key, "private-message")
    review = planner(store, {"source": 1})
    expect_applied(review, review.preview())
    assert not ledger.is_tombstoned("mail", "account", "private-message")           # no upstream wildcard
    assert ledger.is_tombstoned("mail", "account", "private-message", marker)
    ref = blobs.put("private-message", "synthetic private evidence")
    with pytest.raises(Suppressed):
        ledger.upsert_signal(SignalInput("mail", "account", "private-message", "v1", OLD, marker, ref))
    ledger.upsert_signal(SignalInput("mail", "account", "private-message", "v2", OLD, "changed-marker", ref))


def test_expired_replay_removes_bundle_and_cases_but_never_the_source(store):
    ledger, _blobs, _root = store
    sid, ref = signal(store)
    path = replay(store, sid)
    review = planner(store, {"replay": 30})
    preview = review.preview()
    assert expect_applied(review, preview).applied == 1
    assert not path.exists()
    assert ledger.signal_row(sid)["evidence_blob_ref"] == ref
    assert ledger.conn.execute("SELECT COUNT(*) FROM replay_cases").fetchone()[0] == 0
    assert [t["reason"] for t in ledger.tombstones()] == ["retention:replay"]
    assert not ledger.is_tombstoned("mail", "account", "private-message")


def test_preview_is_stable_keyed_and_metadata_only(store):
    sid, _ref = signal(store)
    path = replay(store, sid)
    review = planner(store, {"replay": 30})
    first = review.preview()
    assert first == review.preview()
    assert len(first.digest) == 64
    assert decisions(first, "replay")[0].status == "expired"
    assert decisions(first, "source")[0].status == "policy_needed"
    assert "private-message" not in repr(first) and path.name not in repr(first)
    assert path.exists()


@pytest.mark.parametrize("change", ["file", "policy", "source", "permission", "forged"])
def test_preview_change_refuses_before_effects(store, change):
    ledger, _blobs, _root = store
    sid, _ref = signal(store)
    path = replay(store, sid)
    review = planner(store, {"replay": 30})
    preview = review.preview()
    if change == "file":
        path.write_bytes(path.read_bytes() + b"changed")
    elif change == "policy":
        review = planner(store, {"replay": 31})
    elif change == "source":
        ledger.set_signal_subjects(sid, ("adult",))
    elif change == "permission":
        ledger.grant_consent("adult", "adult", "mail:account", ("view",), "owner")
    digest = "0" * 64 if change == "forged" else preview.digest
    with pytest.raises(RetentionError):
        review.apply(preview, digest, mutation_guard=nullcontext)
    assert path.exists()


def test_current_consent_revoked_hides_inventory_and_refuses_apply(store):
    ledger, _blobs, _root = store
    grant = ledger.grant_consent("adult", "adult", "mail:account", ("view",), "owner")
    sid, _ref = signal(store, subject="adult")
    path = replay(store, sid)
    review = planner(store, {"replay": 30})
    preview = review.preview()
    ledger.revoke_consent(grant)
    assert decisions(review.preview(), "replay") == []
    with pytest.raises(RetentionError):
        review.apply(preview, preview.digest, mutation_guard=nullcontext)
    assert path.exists()


def test_active_evidence_does_not_expire(store):
    ledger, _blobs, _root = store
    sid, _ref = signal(store)
    ledger.put_fact(FactInput(domain="test", predicate="due", value="synthetic", primary_subject_id="owner",
                              observed_at=OLD, evidence=((sid, 0, 5),)))
    assert decisions(planner(store, {"source": 1}).preview(), "source")[0].status == "protected"


def test_all_full_checkpoints_protected_without_filesystem_discovery(store):
    _ledger, _blobs, root = store
    path = root / "secret-full-checkpoint.ack"
    path.write_bytes(b"ACK2synthetic")
    preview = planner(store, {"snapshot": 1}).preview()
    coverage = dict(preview.coverage)
    assert coverage["snapshot"] == "protected"
    assert path.name not in repr(preview) and path.read_bytes() == b"ACK2synthetic"


def test_missing_mutation_guard_refuses(store):
    sid, _ref = signal(store)
    path = replay(store, sid)
    review = planner(store, {"replay": 1})
    preview = review.preview()
    with pytest.raises(RetentionError, match="guard"):
        review.apply(preview, preview.digest)
    assert path.exists()


def test_bounds_refuse_incomplete_inventory(store):
    signal(store, "one")
    signal(store, "two")
    with pytest.raises(RetentionError, match="bound"):
        planner(store, {"source": 1}, max_rows=1).preview()


@pytest.mark.parametrize("failure", [OSError, ValueError, RuntimeError])
def test_interrupted_replay_expiry_hides_private_detail_and_resumes_from_the_journal(store, failure):
    ledger, _blobs, _root = store
    sid, _ref = signal(store)
    path = replay(store, sid)
    original = ledger._replay_deleter

    def interrupted(identities):
        original(identities)
        raise failure("sensitive filename must not escape")

    ledger._replay_deleter = interrupted
    review = planner(store, {"replay": 1})
    preview = review.preview()
    result = review.apply(preview, preview.digest, mutation_guard=nullcontext)
    assert result.interrupted and "sensitive" not in repr(result)
    assert ledger.conn.execute("SELECT state FROM deletion_journal").fetchone()[0] != "receipted"
    with pytest.raises(RetentionError, match="pending erasure"):
        review.preview()
    ledger._replay_deleter = original
    [receipt] = ledger.resume_pending_forgets()
    assert receipt.journal_seq and not path.exists()
    assert ledger.conn.execute("SELECT COUNT(*) FROM replay_cases").fetchone()[0] == 0
    assert decisions(review.preview(), "replay") == []
    assert ledger.signal_row(sid) is not None


@pytest.mark.parametrize("linked", ["file", "directory", "hardlink"])
def test_link_paths_refuse_without_touching_target(store, tmp_path, linked):
    sid, _ref = signal(store)
    path = replay(store, sid)
    target = tmp_path / "foreign-private"
    target.write_bytes(path.read_bytes())
    if linked == "directory":
        path.unlink()
        path.parent.rmdir()
        foreign = tmp_path / "outside"
        foreign.mkdir()
        (foreign / path.name).write_bytes(target.read_bytes())
        path.parent.symlink_to(foreign, target_is_directory=True)
    else:
        path.unlink()
        if linked == "file":
            path.symlink_to(target)
        else:
            path.hardlink_to(target)
    with pytest.raises(RetentionError, match="path"):
        planner(store, {"replay": 1}).preview()
    assert target.exists()


def test_retention_fails_closed_without_native_no_follow_open(store, monkeypatch):
    sid, _ref = signal(store)
    replay(store, sid)
    if sys.platform == "win32":
        from catchup_core import quarantine_fs_windows

        def unavailable(*args, **kwargs):
            raise OSError("native no-follow open unavailable")

        monkeypatch.setattr(quarantine_fs_windows, "open_owner_private_path", unavailable)
        message = "unavailable"
    else:
        monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
        message = "safe no-follow open unavailable"
    with pytest.raises(RetentionError, match=message):
        planner(store, {"replay": 1}).preview()


def test_foreign_root_refuses(store, tmp_path):
    ledger, _blobs, _root = store
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    with pytest.raises(RetentionError, match="root"):
        RetentionPlanner(ledger, RetentionPolicy.from_mapping({}), "owner", NOW, foreign).preview()


def test_shared_evidence_blob_never_expires_behind_another_identity(store):
    ledger, _blobs, _root = store
    sid, ref = signal(store)
    other, _ = ledger.upsert_signal(SignalInput("mail", "account", "another", "v1", OLD, "another-hash", ref))
    ledger.set_signal_subjects(other, ("owner",))
    ledger.set_processing_state(other, ProcessingState.RECONCILED)
    preview = planner(store, {"source": 1}).preview()
    assert len(decisions(preview, "source")) == 2
    assert all(entry.status == "protected" for entry in decisions(preview, "source"))
    assert ledger.signal_row(sid) is not None


def test_unrelated_orphan_alert_dedup_history_is_not_erased_by_expiry(store):
    ledger, _blobs, _root = store
    signal(store)
    ledger.upsert_alert("unresolved-uncertain-effect", domain="test", primary_subject_id="owner", severity="P1")
    preview = planner(store, {"source": 1}).preview()
    assert decisions(preview, "source")[0].status == "protected"


def test_unresolved_commitments_protect_unlinked_source(store):
    ledger, _blobs, _root = store
    signal(store)
    ledger.put_open_item("oi_1", owner="owner", description="unknown completion")
    assert decisions(planner(store, {"source": 1}).preview(), "source")[0].status == "protected"


def test_interrupted_source_expiry_denies_use_then_completes_after_resume(store):
    ledger, _blobs, root = store
    sid, ref = signal(store)
    original = ledger._blob_deleter

    def interrupted(_ref):
        raise OSError("private source filename")

    ledger._blob_deleter = interrupted
    review = planner(store, {"source": 1})
    preview = review.preview()
    result = review.apply(preview, preview.digest, mutation_guard=nullcontext)
    assert result.interrupted and "private" not in repr(result)
    assert ledger.signal_row(sid) is None                      # the database rows go before the files
    assert (root / "blobs" / f"{ref}.blob").exists()           # the file is still there: not receipted
    assert ledger.conn.execute("SELECT state FROM deletion_journal").fetchone()[0] != "receipted"
    with pytest.raises(RetentionError, match="pending erasure"):
        review.preview()
    ledger._blob_deleter = original
    ledger.resume_pending_forgets()
    assert ledger.signal_row(sid) is None and not (root / "blobs" / f"{ref}.blob").exists()
    assert [t["reason"] for t in ledger.tombstones()] == ["retention:source"]


def test_recent_source_version_protects_entire_forget_identity(store):
    ledger, blobs, _root = store
    signal(store)
    ref = blobs.put("new-version", "synthetic newer evidence")
    sid, _ = ledger.upsert_signal(SignalInput("mail", "account", "private-message", "v2", NOW.isoformat(),
                                             "new-marker", ref))
    ledger.set_signal_subjects(sid, ("owner",))
    ledger.set_processing_state(sid, ProcessingState.RECONCILED)
    assert decisions(planner(store, {"source": 1}).preview(), "source")[0].status == "retained"


def test_historical_review_time_cannot_authorize_expired_consent(store):
    ledger, _blobs, root = store
    ledger.grant_consent("adult", "adult", "mail:account", ("view",), "owner", expires_at="2026-01-01T00:00:00Z")
    sid, _ref = signal(store, subject="adult")
    path = replay(store, sid)
    review = RetentionPlanner(ledger, RetentionPolicy.from_mapping({"replay": 1}), "owner",
                              datetime(2021, 1, 1, tzinfo=UTC), root)
    assert decisions(review.preview(), "replay") == []
    assert path.exists()


def test_guard_entry_revocation_refuses_before_any_effect(store):
    ledger, _blobs, _root = store
    grant = ledger.grant_consent("adult", "adult", "mail:account", ("view",), "owner")
    sid, _ref = signal(store, subject="adult")
    path = replay(store, sid)
    review = planner(store)
    preview = review.preview()

    @contextmanager
    def revoked_guard():
        ledger.revoke_consent(grant)
        yield

    with pytest.raises(RetentionError, match="changed"):
        review.apply(preview, preview.digest, mutation_guard=revoked_guard)
    assert path.exists()


def test_shared_replay_requires_all_current_permissions(store):
    ledger, _blobs, _root = store
    sid, _ref = signal(store)
    path = replay(store, sid)
    denied, _ref = signal(store, "denied", subject="adult")
    ledger.conn.execute("INSERT INTO replay_cases VALUES('fbk_1234',?)", (denied,))
    assert decisions(planner(store, {"replay": 1}).preview(), "replay") == []
    assert path.exists()


def test_unknown_unregistered_file_is_never_selected(store):
    _ledger, _blobs, root = store
    directory = root / "replay"
    directory.mkdir()
    foreign = directory / "fbk_foreign.ae1"
    foreign.write_bytes(b"unknown private content")
    review = planner(store, {"replay": 1})
    preview = review.preview()
    assert decisions(preview, "replay") == []
    assert review.apply(preview, preview.digest, mutation_guard=nullcontext).applied == 0
    assert foreign.exists() and foreign.name not in repr(preview)


def test_unknown_replay_identity_refuses_path_traversal(store):
    ledger, _blobs, _root = store
    sid, _ref = signal(store)
    ledger.conn.execute("INSERT INTO feedback(feedback_id,obligation_key,response,reason,actor,recorded_at) "
                        "VALUES('../private','test','useful','encrypted','owner',?)", (OLD,))
    ledger.conn.execute("INSERT INTO replay_cases VALUES('../private',?)", (sid,))
    with pytest.raises(RetentionError, match="path"):
        planner(store, {"replay": 1}).preview()


@pytest.mark.parametrize("time, expected", [("2026-10-24T00:00:00Z", "retained"),
                                            ("2026-10-25T00:00:00Z", "protected"),
                                            ("2026-10-23T00:00:00Z", "expired"),
                                            ("2026-10-23T00:00:00.000001Z", "retained"),
                                            ("2020-01-01", "unsupported")])
def test_registered_replay_reference_time_boundary(store, time, expected):
    ledger, _blobs, _root = store
    sid, _ref = signal(store)
    replay(store, sid)
    ledger.conn.execute("UPDATE feedback SET recorded_at=?", (time,))
    assert decisions(planner(store, {"replay": 1}).preview(), "replay")[0].status == expected


def test_known_classes_without_proven_api_remain_unsupported(store):
    ledger, _blobs, root = store
    sid, _ref = signal(store, connector="documents")
    ledger.conn.execute("INSERT INTO corpus_cases VALUES('case_1234',?)", (sid,))
    directory = root / "corpus"
    directory.mkdir()
    assert ledger._cipher is not None
    private_atomic_bytes(directory / "case_1234.ae1", ledger._cipher.encrypt(b"synthetic", aad=b"case_1234")[0])
    ledger.publish_briefing("cu_1", "synthetic")
    ledger.conn.execute("INSERT INTO briefing_signals VALUES('cu_1',?)", (sid,))
    ledger.put_fact(FactInput(domain="test", predicate="value", value="synthetic", primary_subject_id="owner",
                              observed_at=OLD, evidence=((sid, 0, 5),)))
    review = planner(store, dict.fromkeys(("source", "document", "corpus", "replay", "briefing", "trace",
                                          "snapshot", "history"), 1))
    preview = review.preview()
    assert decisions(preview, "document")[0].status == "protected"
    for artifact_class in ("corpus", "briefing", "history"):
        assert decisions(preview, artifact_class)[0].status == "unsupported"
    assert dict(preview.coverage)["trace"] == "unsupported"
    assert review.apply(preview, preview.digest, mutation_guard=nullcontext).applied == 0


@pytest.mark.parametrize("missing", ["blob", "archive", "replay_of_source", "replay"])
def test_missing_erasure_path_refuses_before_any_effect(store, missing):
    ledger, _blobs, _root = store
    sid, _ref = signal(store)
    artifact_class = "source"
    if missing == "blob":
        ledger._blob_deleter = None
    elif missing == "archive":
        ledger.publish_briefing("cu_1", "synthetic")
        ledger.conn.execute("INSERT INTO briefing_signals VALUES('cu_1',?)", (sid,))
    elif missing == "replay_of_source":
        replay(store, sid)
        ledger._replay_deleter = None
    else:
        replay(store, sid)
        ledger._replay_deleter = None
        artifact_class = "replay"
    review = planner(store, {artifact_class: 1})
    preview = review.preview()
    assert decisions(preview, artifact_class)[0].status == "expired"
    before = ledger.conn.total_changes
    with pytest.raises(RetentionError, match="expiry_erasure_path_unavailable"):
        review.apply(preview, preview.digest, mutation_guard=nullcontext)
    assert ledger.signal_row(sid) is not None and ledger.tombstones() == []
    assert ledger.conn.total_changes == before


def test_unknown_source_attribution_and_nonowner_are_not_authorized(store):
    ledger, _blobs, root = store
    sid, _ref = signal(store)
    ledger.conn.execute("DELETE FROM signal_access WHERE signal_id=?", (sid,))
    assert decisions(planner(store, {"source": 1}).preview(), "source") == []
    with pytest.raises(RetentionError, match="owner"):
        RetentionPlanner(ledger, RetentionPolicy.from_mapping({}), "adult", NOW, root).preview()


def test_unresolved_interpretation_cannot_expire(store):
    ledger, _blobs, _root = store
    sid, _ref = signal(store)
    ledger.conn.execute("UPDATE signals SET processing_state='discovered' WHERE signal_id=?", (sid,))
    assert decisions(planner(store, {"source": 1}).preview(), "source")[0].status == "protected"


def test_changed_preview_body_cannot_reuse_digest(store):
    sid, _ref = signal(store)
    replay(store, sid)
    review = planner(store, {"replay": 1})
    preview = review.preview()
    with pytest.raises(RetentionError, match="changed"):
        review.apply(replace(preview, entries=()), preview.digest, mutation_guard=nullcontext)


@pytest.mark.parametrize("kwargs", [{"max_rows": True}, {"max_rows": 0}, {"max_bytes": 0},
                                     {"now": datetime(2026, 1, 1)}, {"viewer": ""}])
def test_invalid_planner_configuration_refuses(store, kwargs):
    ledger, _blobs, root = store
    options = {"ledger": ledger, "policy": RetentionPolicy.from_mapping({}), "viewer": "owner",
               "now": NOW, "root": root, **kwargs}
    with pytest.raises(RetentionError):
        RetentionPlanner(**options)


def test_artifact_byte_bound_and_wrong_envelope_refuse(store):
    sid, _ref = signal(store)
    path = replay(store, sid)
    path.write_bytes(b"plain private content")
    with pytest.raises(RetentionError, match="envelope"):
        planner(store, {"replay": 1}).preview()
    path.write_bytes(b"AE1" + b"a" * 65536)
    with pytest.raises(RetentionError, match="bound"):
        planner(store, {"replay": 1}, max_bytes=32000).preview()


def test_failed_guard_does_not_expose_private_error(store):
    sid, _ref = signal(store)
    path = replay(store, sid)
    review = planner(store)
    preview = review.preview()

    def unavailable_guard():
        raise OSError("secret installation path")

    result = review.apply(preview, preview.digest, mutation_guard=unavailable_guard)
    assert result.interrupted and "secret" not in repr(result) and path.exists()


def test_multiple_expired_sources_each_get_their_own_journal_receipt(store):
    ledger, _blobs, root = store
    first, first_ref = signal(store, "first")
    second, second_ref = signal(store, "second")
    review = planner(store, {"source": 1})
    assert expect_applied(review, review.preview()).applied == 2
    assert ledger.signal_row(first) is None and ledger.signal_row(second) is None
    assert not any((root / "blobs" / f"{ref}.blob").exists() for ref in (first_ref, second_ref))
    assert [row[0] for row in ledger.conn.execute("SELECT state FROM deletion_journal")] == ["receipted"] * 2


def test_file_inventory_is_bounded_in_aggregate(store):
    sid, _ref = signal(store)
    path = replay(store, sid)
    other = replay(store, sid, identity="fbk_5678")
    for target in (path, other):
        target.write_bytes(b"AE1" + b"a" * 32768)
    with pytest.raises(RetentionError, match="bound"):
        planner(store, {"replay": 1}, max_bytes=50000).preview()


def old_copy(ledger, root):
    path = root / "old-ledger.db"
    with closing(sqlite3.connect(path)) as old:
        ledger.conn.backup(old)
    return path


def test_old_restore_of_expired_source_stays_locked_until_the_tombstone_is_replayed(store):
    ledger, blobs, root = store
    sid, ref = signal(store)
    old_path = old_copy(ledger, root)
    saved_blob = (root / "blobs" / f"{ref}.blob").read_bytes()
    review = planner(store, {"source": 1})
    expect_applied(review, review.preview())
    journal = ledger.tombstones()
    (root / "blobs" / f"{ref}.blob").write_bytes(saved_blob)          # an older backup brings the bytes back
    restored = Ledger(old_path, id_key=ledger._id_key, cipher=ledger._cipher, sealed=ledger._sealed,
                      owner_id="owner", clock=lambda: NOW.isoformat(), blob_deleter=blobs.delete).open()
    try:
        assert restored.check_restore() is False and restored.locked
        assert restored.replay_tombstones(journal) is True
        assert restored.signal_row(sid) is None
        assert not (root / "blobs" / f"{ref}.blob").exists()
        assert restored.is_tombstoned("mail", "account", "private-message",
                                      ids.content_hmac(ledger._id_key, "private-message"))
    finally:
        restored.close()


def test_old_restore_of_expired_replay_cannot_bring_the_bundle_back(store):
    ledger, _blobs, root = store
    sid, _ref = signal(store)
    path = replay(store, sid)
    saved = path.read_bytes()
    old_path = old_copy(ledger, root)
    review = planner(store, {"replay": 1})
    expect_applied(review, review.preview())
    journal = ledger.tombstones()
    path.write_bytes(saved)
    erased = []
    restored = Ledger(old_path, id_key=ledger._id_key, cipher=ledger._cipher, sealed=ledger._sealed,
                      owner_id="owner", clock=lambda: NOW.isoformat()).open()
    restored._replay_deleter = lambda identities: erased.extend(identities) or path.unlink()
    try:
        assert restored.check_restore() is False
        assert restored.replay_tombstones(journal) is True
        assert erased == ["fbk_1234"] and not path.exists()
        assert restored.conn.execute("SELECT COUNT(*) FROM replay_cases").fetchone()[0] == 0
    finally:
        restored.close()


def test_snapshot_restore_privacy_replay_removes_expired_replay_and_blob(store):
    from catchup_core.backup import apply_privacy_history
    ledger, _blobs, root = store
    sid, ref = signal(store)
    replay(store, sid)
    old_path = old_copy(ledger, root)
    expect_applied(planner(store, {"source": 1, "replay": 1}), planner(store, {"source": 1, "replay": 1}).preview())
    removed = []
    with closing(sqlite3.connect(old_path)) as db:
        apply_privacy_history(db, removed.append, ledger._sealed.read(), {"tombstones": ledger.tombstones(),
                                                                         "consent": []})
        assert db.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM replay_cases").fetchone()[0] == 0
    assert f"blobs/{ref}.blob" in removed and "replay/fbk_1234.ae1" in removed


def test_unreleased_full_checkpoints_and_unregistered_files_survive_expiry(store):
    ledger, _blobs, root = store
    signal(store)
    checkpoint = root / "full-checkpoint-unreleased.ack"
    checkpoint.write_bytes(b"ACK2synthetic-full-checkpoint")
    foreign = root / "blobs" / "evd_unregistered.blob"
    foreign.write_bytes(b"AE1 unregistered")
    review = planner(store, {"source": 1, "snapshot": 1})
    preview = review.preview()
    assert dict(preview.coverage)["snapshot"] == "protected"
    assert expect_applied(review, preview).applied == 1
    assert checkpoint.read_bytes() == b"ACK2synthetic-full-checkpoint" and foreign.exists()
    assert checkpoint.name not in repr(preview)


def test_retention_never_releases_an_ack2_at_registered_replay_path(store):
    sid, _ref = signal(store)
    path = replay(store, sid)
    path.write_bytes(b"ACK2full-encrypted-checkpoint")
    with pytest.raises(RetentionError, match="envelope"):
        planner(store, {"replay": 1}).preview()
    assert path.exists()


@pytest.mark.parametrize("periods", [[], (("unknown", 1),), (("source", 0),),
                                     (("source", 1), ("source", 2)), (("source", 1), ("replay", 2)),
                                     (("source", 1, 2),)])
def test_direct_policy_construction_cannot_bypass_validation(periods):
    with pytest.raises(RetentionError, match="policy"):
        RetentionPolicy(periods)


@pytest.mark.parametrize("value", [None, "2026-13-01T00:00:00Z", "2026-10-24T00:00:00"])
def test_explicit_datetime_required_for_planner(store, value):
    ledger, _blobs, root = store
    with pytest.raises(RetentionError, match="time"):
        RetentionPlanner(ledger, RetentionPolicy.from_mapping({}), "owner", value, root)


def test_missing_registered_blob_directory_is_an_exact_missing_artifact(store):
    _ledger, _blobs, root = store
    _sid, ref = signal(store)
    (root / "blobs" / f"{ref}.blob").unlink()
    (root / "blobs").rmdir()
    review = planner(store, {"source": 1})
    preview = review.preview()
    assert decisions(preview, "source")[0].status == "expired"
    assert expect_applied(review, preview).applied == 1


def test_unknown_evidence_reference_refuses_without_diagnostic_filename(store):
    ledger, _blobs, _root = store
    signal(store)
    ledger.conn.execute("UPDATE signals SET evidence_blob_ref='../secret'")
    with pytest.raises(RetentionError) as caught:
        planner(store, {"source": 1}).preview()
    assert "secret" not in str(caught.value)


def test_unknown_corpus_identity_refuses_without_path_use(store):
    ledger, _blobs, _root = store
    sid, _ref = signal(store)
    ledger.conn.execute("INSERT INTO corpus_cases VALUES('../secret',?)", (sid,))
    with pytest.raises(RetentionError, match="path"):
        planner(store, {"corpus": 1}).preview()


def test_history_with_revoked_subject_and_unattributed_briefing_stay_hidden(store):
    ledger, _blobs, _root = store
    ledger.put_fact(FactInput(domain="test", predicate="value", value="synthetic", primary_subject_id="adult",
                              observed_at=OLD, confidentiality=Confidentiality.RESTRICTED))
    ledger.publish_briefing("cu_hidden", "synthetic")
    preview = planner(store, {"history": 1, "briefing": 1}).preview()
    assert decisions(preview, "history") == decisions(preview, "briefing") == []


def test_inventory_byte_bound_and_unknown_values_refuse(store):
    ledger, _blobs, _root = store
    signal(store)
    with pytest.raises(RetentionError, match="bound"):
        planner(store, {"source": 1}, max_bytes=10).preview()
    ledger.conn.execute("UPDATE signals SET content_hmac=?", (sqlite3.Binary(b"unknown"),))
    with pytest.raises(RetentionError, match="unavailable"):       # a BLOB where text is required still refuses
        planner(store, {"source": 1}).preview()


def test_missing_ledger_reports_no_path(store, monkeypatch):
    ledger, _blobs, _root = store
    if sys.platform == "win32":
        original = type(ledger._path).lstat

        def missing(path, *args, **kwargs):
            if path == ledger._path:
                raise FileNotFoundError
            return original(path, *args, **kwargs)

        monkeypatch.setattr(type(ledger._path), "lstat", missing)
    else:
        ledger._path.unlink()
    with pytest.raises(RetentionError, match="unavailable") as caught:
        planner(store, {"source": 1}).preview()
    assert str(ledger._path) not in str(caught.value)


def test_linked_ledger_is_not_owned_by_retention_root(store):
    ledger, _blobs, root = store
    (root / "foreign-ledger.db").hardlink_to(ledger._path)
    with pytest.raises(RetentionError, match="ledger path"):
        planner(store, {"source": 1}).preview()


def test_document_without_dependencies_has_no_proven_standalone_erasure_api(store):
    signal(store, connector="documents")
    entry = decisions(planner(store, {"document": 1}).preview(), "document")[0]
    assert entry.status == "unsupported" and entry.reason == "document_capture_erasure_inventory_required"


def test_large_registered_envelope_is_hashed_in_bounded_chunks(store):
    ledger, _blobs, _root = store
    sid, _ref = signal(store)
    path = replay(store, sid)
    assert ledger._cipher is not None
    path.write_bytes(ledger._cipher.encrypt(b"synthetic" * 12000, aad=b"replay:fbk_1234")[0])
    review = planner(store, {"replay": 1})
    preview = review.preview()
    assert decisions(preview, "replay")[0].status == "expired"
    assert preview == review.preview()
    assert expect_applied(review, preview).applied == 1
    assert not path.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows security descriptors")
@pytest.mark.parametrize("artifact", ["blob", "replay"])
def test_new_retention_artifacts_have_protected_owner_private_security(store, artifact):
    from catchup_core.quarantine_fs_windows import open_owner_private_path
    sid, ref = signal(store)
    path = (store[2] / "blobs" / f"{ref}.blob") if artifact == "blob" else replay(store, sid)
    with open_owner_private_path(path) as opened:
        assert opened.snapshot().size == path.stat().st_size


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows bounded streaming")
def test_native_retention_hashes_chunks_without_whole_file_reads(store, monkeypatch):
    from catchup_core.quarantine_fs_windows import WindowsQuarantineFile
    sid, _ref = signal(store)
    path = replay(store, sid)
    path.write_bytes(b"AE1" + b"x" * 200000)
    sizes = []
    original = WindowsQuarantineFile.read_chunk

    def chunk(opened, size):
        sizes.append(size)
        return original(opened, size)

    def whole_file(*args, **kwargs):
        pytest.fail("retention must not read a complete native file into memory")

    monkeypatch.setattr(WindowsQuarantineFile, "read_chunk", chunk)
    monkeypatch.setattr(WindowsQuarantineFile, "read", whole_file)
    preview = planner(store, {"replay": 1}).preview()
    assert decisions(preview, "replay")[0].status == "expired"
    assert len(sizes) > 3 and max(sizes) <= 65536


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows historical ACL refusal")
def test_native_retention_refuses_historical_inherited_acl_without_migration(store):
    sid, _ref = signal(store)
    path = replay(store, sid)
    data = path.read_bytes()
    path.unlink()
    path.write_bytes(data)
    before = path.stat()
    with pytest.raises(RetentionError):
        planner(store, {"replay": 1}).preview()
    assert path.read_bytes() == data
    assert path.stat().st_mtime_ns == before.st_mtime_ns


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows streaming mutation checks")
def test_native_retention_refuses_content_changed_during_streaming(store, monkeypatch):
    from catchup_core.quarantine_fs_windows import WindowsQuarantineFile
    sid, _ref = signal(store)
    path = replay(store, sid)
    original = WindowsQuarantineFile.read_chunk
    changed = False

    def chunk(opened, size):
        nonlocal changed
        data = original(opened, size)
        if opened.name == path.name and not changed:
            changed = True
            path.write_bytes(b"AE1changed")
        return data

    monkeypatch.setattr(WindowsQuarantineFile, "read_chunk", chunk)
    with pytest.raises(RetentionError):
        planner(store, {"replay": 1}).preview()
    assert changed and path.read_bytes() == b"AE1changed"
    assert store[0].conn.execute("SELECT count(*) FROM feedback").fetchone()[0] == 1


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows protected publication")
def test_private_writer_replaces_only_already_private_files(tmp_path):
    from catchup_core.quarantine_fs_windows import QuarantineFilesystemError, open_owner_private_path
    path = tmp_path / "synthetic.ae1"
    private_atomic_bytes(path, b"AE1first")
    private_atomic_bytes(path, b"AE1second")
    with open_owner_private_path(path) as opened:
        assert opened.read(100) == b"AE1second"
    path.unlink()
    path.write_bytes(b"AE1historical")
    with pytest.raises(QuarantineFilesystemError, match="DACL"):
        private_atomic_bytes(path, b"AE1replacement")
    assert path.read_bytes() == b"AE1historical"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows staged publication")
def test_private_writer_cleans_its_staged_file_on_publication_failure(tmp_path, monkeypatch):
    from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory

    def refused(*args, **kwargs):
        raise RuntimeError("synthetic refused publication")

    monkeypatch.setattr(WindowsQuarantineDirectory, "publish_write_once", refused)
    with pytest.raises(RuntimeError, match="synthetic refused publication"):
        private_atomic_bytes(tmp_path / "synthetic.ae1", b"AE1synthetic")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows staged publication")
@pytest.mark.parametrize("failure", ["write_all", "flush", "replace_file"])
def test_private_writer_failure_preserves_previous_ciphertext_and_removes_stage(tmp_path, monkeypatch, failure):
    from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory, WindowsQuarantineFile

    path = tmp_path / "synthetic.ae1"
    private_atomic_bytes(path, b"AE1previous")

    def refused(*args, **kwargs):
        raise OSError("synthetic storage failure")

    target = WindowsQuarantineDirectory if failure == "replace_file" else WindowsQuarantineFile
    monkeypatch.setattr(target, failure, refused)
    with pytest.raises(OSError, match="synthetic storage failure"):
        private_atomic_bytes(path, b"AE1replacement")
    assert path.read_bytes() == b"AE1previous"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows staged publication")
def test_private_writer_preserves_failure_after_stage_has_already_disappeared(tmp_path, monkeypatch):
    from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory

    def removed(parent, stage, target, *, expected_identity):
        parent.delete_file(stage, expected_identity=expected_identity)
        raise OSError("synthetic stage removed before publication")

    monkeypatch.setattr(WindowsQuarantineDirectory, "publish_write_once", removed)
    with pytest.raises(OSError, match="stage removed before publication"):
        private_atomic_bytes(tmp_path / "synthetic.ae1", b"AE1synthetic")
    assert list(tmp_path.iterdir()) == []


def test_private_writer_preserves_posix_atomic_writer(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from catchup_core import backup, blobs
    calls = []
    monkeypatch.setattr(blobs, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(backup, "atomic_bytes", lambda path, data: calls.append((path, data)))
    path = tmp_path / "synthetic.ae1"
    private_atomic_bytes(path, b"AE1synthetic")
    assert calls == [(path, b"AE1synthetic")]


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows replay publication")
def test_runtime_replay_writer_and_review_preserve_private_security(store):
    from catchup_core import views
    from catchup_core.quarantine_fs_windows import open_owner_private_path
    ledger, blobs, root = store
    sid, _ref = signal(store)
    fid = ledger.put_fact(FactInput(domain="test", predicate="value", value="synthetic",
                                   primary_subject_id="owner", observed_at=OLD, evidence=((sid, 0, 1),)))
    ledger.upsert_alert("synthetic", domain="test", primary_subject_id="owner", severity="P1", fact_ids=[fid])
    feedback_id = views.feedback(ledger, "owner", "synthetic", "dismiss", "synthetic", NOW)
    path = views.replay_case(ledger, blobs, "owner", "synthetic", "dismiss", "synthetic",
                             feedback_id, NOW, root / "replay")
    with open_owner_private_path(path) as opened:
        first = opened.identity
    views.review_feedback(ledger, "owner", root / "replay", feedback_id, "correct", NOW)
    with open_owner_private_path(path) as opened:
        assert opened.identity != first
    assert planner(store).preview().entries


def test_shared_content_marker_across_identities_never_expires(store):
    ledger, blobs, _root = store
    signal(store)
    ref = blobs.put("other-message", "synthetic other evidence")
    sid, _ = ledger.upsert_signal(SignalInput("mail", "account", "other-message", "v1", OLD,
                                              ids.content_hmac(ledger._id_key, "private-message"), ref))
    ledger.set_signal_subjects(sid, ("owner",))
    ledger.set_processing_state(sid, ProcessingState.RECONCILED)
    assert {e.status for e in decisions(planner(store, {"source": 1}).preview(), "source")} == {"protected"}


def test_retention_periods_are_unset_unless_the_owner_configures_them():
    for absent in (None, {}, {"other": 1}, {"retention": None}):
        assert RetentionPolicy.from_config(absent) == RetentionPolicy.unset()
    assert all(RetentionPolicy.unset().days_for(name) is None for name in
               ("source", "document", "corpus", "replay", "briefing", "trace", "snapshot", "history"))
    assert RetentionPolicy.from_config({"retention": {"replay": 30}}).days_for("replay") == 30
    with pytest.raises(RetentionError):
        RetentionPolicy.from_config({"retention": {"source": 0}})


def test_unset_periods_expire_nothing_even_when_approved(store):
    ledger, _blobs, _root = store
    sid, _ref = signal(store)
    replay(store, sid)
    review = RetentionPlanner(ledger, RetentionPolicy.unset(), "owner", NOW, store[2])
    preview = review.preview()
    assert {e.status for e in preview.entries} == {"policy_needed"}
    result = review.apply(preview, preview.digest, mutation_guard=nullcontext)
    assert result.applied == 0 and result.reason == "no_applicable_expiry"
    assert ledger.signal_row(sid) is not None and ledger.tombstones() == []


def test_repeating_an_expiry_that_is_already_recorded_is_refused(store):
    ledger, _blobs, _root = store
    sid, _ref = signal(store)
    marker = ledger.signal_row(sid)["content_hmac"]
    ledger.expire_content(marker)
    with pytest.raises(Exception, match="already recorded"):
        ledger.expire_content(marker)


def killed_expiry(root, ready):
    import os
    from pathlib import Path

    original = Ledger.available_migrations
    Ledger.available_migrations = staticmethod(lambda: [(n, p) for n, p in original() if n <= 16])
    root = Path(root)
    key = ids.HmacKey(1, b"r" * 32)
    cipher = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"s" * 32}))
    ledger = Ledger(root / "ledger.db", id_key=key, cipher=cipher, owner_id="owner",
                    consent_subjects=frozenset({"adult"}), known_subjects=frozenset({"owner", "adult"}),
                    clock=lambda: NOW.isoformat(), blob_deleter=lambda _ref: os._exit(9)).open()
    ready.put(True)
    review = RetentionPlanner(ledger, RetentionPolicy.from_mapping({"source": 1}), "owner", NOW, root)
    preview = review.preview()
    review.apply(preview, preview.digest, mutation_guard=nullcontext)


def test_real_process_killed_mid_expiry_resumes_from_the_journal(store):
    import multiprocessing
    ledger, blobs, root = store
    sid, ref = signal(store)
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    worker = context.Process(target=killed_expiry, args=(str(root), ready))
    worker.start()
    try:
        assert ready.get(timeout=30)
        worker.join(30)
        assert worker.exitcode == 9
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join()
    assert ledger.conn.execute("SELECT state FROM deletion_journal").fetchone()[0] != "receipted"
    assert (root / "blobs" / f"{ref}.blob").exists()
    ledger.resume_pending_forgets()
    assert ledger.signal_row(sid) is None and not (root / "blobs" / f"{ref}.blob").exists()
    assert ledger.conn.execute("SELECT state FROM deletion_journal").fetchone()[0] == "receipted"


def test_reading_an_artifact_may_update_its_access_time_without_looking_like_a_path_change(store, monkeypatch):
    import os

    from catchup_core import retention
    sid, _ref = signal(store)
    replay(store, sid)
    real = os.fstat
    calls = []

    def touched(descriptor):
        info = real(descriptor)
        calls.append(1)
        if len(calls) % 2 == 0:
            fields = [*info, info.st_atime, info.st_mtime, info.st_ctime,
                      info.st_atime_ns, info.st_mtime_ns, info.st_ctime_ns]
            fields[7] += 5          # a read advanced st_atime; nothing else changed
            fields[13] += 5_000_000_000
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(retention.os, "fstat", touched)
    assert decisions(planner(store, {"replay": 1}).preview(), "replay")[0].status == "expired"


def test_snapshot_restore_replays_content_keyed_tombstones_with_null_identity(store):
    """Given a tombstone keyed by content only, when an old snapshot replays it, then the signal goes and nothing crashes."""
    from catchup_core.backup import apply_privacy_history
    ledger, _blobs, root = store
    sid, ref = signal(store)
    marker = ledger.signal_row(sid)["content_hmac"]
    old_path = old_copy(ledger, root)
    ledger.expire_content(marker)
    journal = ledger.tombstones()
    content_keyed = next(row for row in journal if row["content_hmac"] is not None)
    assert content_keyed["connector"] is None and content_keyed["upstream_id"] is None
    removed = []
    with closing(sqlite3.connect(old_path)) as db:
        apply_privacy_history(db, removed.append, ledger._sealed.read(), {"tombstones": journal, "consent": []})
        assert db.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0
    assert f"blobs/{ref}.blob" in removed
    assert not any("None" in name for name in removed)


@pytest.mark.parametrize("connector", ["documents", "capture", "retention"])
def test_snapshot_restore_ignores_artifact_removal_for_tombstones_without_an_upstream(store, connector):
    from catchup_core.backup import apply_privacy_history
    ledger, _blobs, root = store
    old_path = old_copy(ledger, root)
    entry = {"tombstone_id": "tmb_null", "connector": connector, "account_scope": "replay", "upstream_id": None,
             "content_hmac": None, "created_at": NOW.isoformat(), "reason": "synthetic"}
    removed = []
    checkpoint = type("Checkpoint", (), {"checkpoint_id": 1, "permission_generation": 0,
        "tombstone_digest": hashlib.sha256(b"tmb_null").hexdigest()})()
    with closing(sqlite3.connect(old_path)) as db:
        apply_privacy_history(db, removed.append, checkpoint, {"tombstones": [entry], "consent": []})
    assert removed == []


def test_preview_describes_blob_columns_and_a_changed_blob_changes_the_digest(store):
    """Given a table with a BLOB column, when previewed, then it works and a changed blob changes the digest."""
    ledger, _blobs, _root = store
    signal(store)
    ledger.conn.execute("CREATE TABLE probe_blobs(id TEXT, payload BLOB)")
    ledger.conn.execute("INSERT INTO probe_blobs VALUES('a', ?)", (b"\x00\x01ciphertext",))
    first = planner(store).preview()
    ledger.conn.execute("UPDATE probe_blobs SET payload=?", (b"\x00\x02ciphertext",))
    assert planner(store).preview().digest != first.digest


def test_row_bound_applies_to_each_table_and_names_the_table_that_is_too_large(store):
    """Given two modest tables and one oversized table, then only the oversized one blocks the preview."""
    ledger, _blobs, _root = store
    signal(store)
    for table in ("probe_one", "probe_two"):
        ledger.conn.execute(f"CREATE TABLE {table}(id INTEGER)")
        ledger.conn.executemany(f"INSERT INTO {table} VALUES(?)", [(n,) for n in range(40)])
    existing = max(ledger.conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                   for (name,) in ledger.conn.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    bound = max(existing, 45)
    assert planner(store, max_rows=bound).preview().entries         # 80+ probe rows in total, under the bound each
    ledger.conn.execute("CREATE TABLE probe_big(id INTEGER)")
    ledger.conn.executemany("INSERT INTO probe_big VALUES(?)", [(n,) for n in range(bound + 1)])
    with pytest.raises(RetentionError, match="table probe_big exceeds the row bound"):
        planner(store, max_rows=bound).preview()


def test_preview_works_on_a_ledger_with_every_migration_applied(tmp_path):
    """Given the current schema (usage, cache, admission tables), when previewed, then it needs no policy edit."""
    root = tmp_path.resolve()
    key = ids.HmacKey(1, b"r" * 32)
    cipher = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"s" * 32}))
    blobs = EncryptedBlobStore(root / "blobs", cipher, key)
    ledger = Ledger(root / "ledger.db", id_key=key, cipher=cipher, owner_id="owner",
                    consent_subjects=frozenset({"adult"}), known_subjects=frozenset({"owner", "adult"}),
                    clock=lambda: NOW.isoformat(), blob_deleter=blobs.delete).open()
    try:
        ledger.migrate()
        preview = RetentionPlanner(ledger, RetentionPolicy.unset(), "owner", NOW, root).preview()
        assert all(status == "policy_needed" for klass, status in preview.coverage if klass != "snapshot")
    finally:
        ledger.close()


def test_explicit_retain_all_is_a_policy_not_an_unset_period():
    policy = RetentionPolicy.from_config({'retention': {'mode': 'retain_all'}})
    assert policy.retain_all is True
    assert policy.periods == ()
    for invalid in ({'mode': 'retain_all', 'source': 30}, {'mode': 'delete_all'}, {'mode': True}):
        with pytest.raises(RetentionError):
            RetentionPolicy.from_mapping(invalid)


def test_retain_all_preview_is_explicit_and_never_selects_expiry(store):
    signal(store)
    planner = RetentionPlanner(store[0], RetentionPolicy.from_mapping({'mode': 'retain_all'}),
                               viewer='owner', now=NOW, root=store[2])
    preview = planner.preview()
    assert preview.entries and not any(entry.status == 'expired' for entry in preview.entries)
    assert all(entry.status in {'retained', 'protected'} for entry in preview.entries)
    assert all(status in {'retained_by_policy', 'protected'} for _, status in preview.coverage)
