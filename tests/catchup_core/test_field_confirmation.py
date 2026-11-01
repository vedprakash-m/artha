from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from catchup_core import crypto, ids, ledger as lg
from catchup_core.field_confirmation import FieldBinding, FieldReviewStore

KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"7" * 32}))
NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)
TEXT = "Subject: adult\nPage 1\nDocument amount: 123.45 EUR"
MIGRATIONS = Path(lg.__file__).parent / "migrations"


@pytest.fixture
def case(tmp_path):
    with lg.Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER,
                   consent_subjects=frozenset({"adult"}),
                   known_subjects=frozenset({"owner", "adult", "other"}),
                   clock=lambda: NOW.isoformat()).open() as led:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            led.conn.executescript(path.read_text())
        sid, _ = led.upsert_signal(lg.SignalInput("documents", "manual", "doc_synthetic:p1", "v1",
            NOW.isoformat(), ids.content_hmac(KEY, TEXT), "opaque-encrypted-blob"))
        led.set_signal_subjects(sid, ("adult",))
        led.observe_source_versions([sid], complete=True)
        led.grant_consent("adult", "adult", "documents:manual", ["view", "correct"], "owner")
        led.grant_consent("adult", "adult", "finance", ["view", "correct"], "owner")
        start = TEXT.encode().index(b"123.45")
        binding = FieldBinding("adult", "finance", "amount", '"123.45"', sid, "v1",
                               (start, start + 6), "doc_synthetic", 1, ids.content_hmac(KEY, TEXT))
        review = FieldReviewStore(led, "owner", lambda: NOW, source_text=lambda _sid: TEXT)
        yield led, review, binding


def test_confirmation_is_exact_encrypted_and_independent_of_user_assertions(case):
    led, review, binding = case
    fid = led.put_fact(lg.FactInput("finance", "amount", "user assertion", "adult", NOW.isoformat(),
                                  provenance=lg.Provenance.USER_CONFIRMED))
    before = led.get_fact(fid, "owner")
    assert not review.verified(binding)
    preview = review.preview(binding)
    assert preview["permission_generation"] == led._permission_generation()
    assert preview["expires_in_seconds"] == 1800
    receipt = review.confirm(binding, preview["apply_token"])
    assert receipt == review.confirm(binding, preview["apply_token"])
    assert review.verified(binding)
    assert led.get_fact(fid, "owner") == before
    payloads = led.conn.execute("SELECT payload FROM document_field_requirements").fetchall()
    assert payloads and all("123.45" not in row[0] for row in payloads)
    assert not review.verified(replace(binding, typed_value_json='"123.46"'))


@pytest.mark.parametrize("change", [
    {"typed_value_json": '"123.46"'}, {"predicate": "balance"}, {"domain": "health"},
    {"subject_id": "other"}, {"span": (0, 1)}, {"document_id": "doc_other"}, {"page": 2},
    {"signal_version": "v2"}, {"provenance_hmac": "forged"},
])
def test_substitution_cannot_use_preview(case, change):
    _, review, binding = case
    token = review.preview(binding)["apply_token"]
    with pytest.raises((ValueError, lg.LedgerError)):
        review.confirm(replace(binding, **change), token)
    assert not review.verified(binding)


@pytest.mark.parametrize("change", [
    {"span": (True, 4)}, {"span": [0, 1]}, {"span": (-1, 1)}, {"span": (2, 2)},
    {"page": True}, {"page": 0}, {"subject_id": ""}, {"typed_value_json": "NaN"},
    {"typed_value_json": '{"x":1,"x":2}'}, {"typed_value_json": True},
    {"fact_id": "fct_missing"}, {"fact_version": "v1"},
])
def test_literal_types_and_invalid_values_are_rejected(case, change):
    _, review, binding = case
    with pytest.raises((ValueError, TypeError)):
        review.preview(replace(binding, **change))


def test_utf8_boundaries_and_trusted_text_digest(case):
    led, review, binding = case
    text = "\u00e9uro"
    marker = ids.content_hmac(KEY, text)
    led.conn.execute("UPDATE signals SET content_hmac=? WHERE signal_id=?", (marker, binding.signal_id))
    review = FieldReviewStore(led, "owner", lambda: NOW, source_text=lambda _sid: text)
    with pytest.raises(ValueError):
        review.preview(replace(binding, provenance_hmac=marker, span=(1, 3)))
    valid = replace(binding, provenance_hmac=marker, span=(0, 2))
    token = review.preview(valid)["apply_token"]
    review.confirm(valid, token)
    assert review.verified(valid)
    other = FieldReviewStore(led, "owner", lambda: NOW, source_text=lambda _sid: "changed")
    assert not other.verified(valid)


def test_expiry_future_wrong_actor_and_malformed_tokens(case):
    led, review, binding = case
    token = review.preview(binding)["apply_token"]
    for instant in (NOW - timedelta(seconds=1), NOW + timedelta(seconds=1801)):
        other = FieldReviewStore(led, "owner", lambda: instant, source_text=lambda _sid: TEXT)
        with pytest.raises(ValueError):
            other.confirm(binding, token)
    for token in ("", "bad", "1:forged", None, True):
        with pytest.raises(ValueError):
            review.confirm(binding, token)
    other = FieldReviewStore(led, "adult", lambda: NOW, source_text=lambda _sid: TEXT)
    with pytest.raises(lg.PermissionDenied):
        other.preview(binding)


def test_withdrawal_is_exact_and_does_not_resurrect_old_tokens(case):
    _, review, binding = case
    token = review.preview(binding)["apply_token"]
    review.confirm(binding, token)
    review.withdraw(binding, review.preview(binding)["apply_token"])
    assert not review.verified(binding)
    with pytest.raises(ValueError):
        review.confirm(binding, token)
    review.confirm(binding, review.preview(binding)["apply_token"])
    assert review.verified(binding)


def test_permissions_rechecked_but_unrelated_generation_does_not_erase_receipt(case):
    led, review, binding = case
    token = review.preview(binding)["apply_token"]
    review.confirm(binding, token)
    led.grant_consent("other", "other", "health", ["view"], "owner")
    assert review.verified(binding)
    preview = review.preview(binding)
    grant = led.conn.execute("SELECT consent_id FROM consent WHERE scope='documents:manual'").fetchone()[0]
    led.revoke_consent(grant)
    assert not review.verified(binding)
    with pytest.raises(lg.PermissionDenied):
        review.confirm(binding, preview["apply_token"])


def test_retained_obsolete_signal_and_deleted_signal_cannot_verify(case):
    led, review, binding = case
    review.confirm(binding, review.preview(binding)["apply_token"])
    sid, _ = led.upsert_signal(lg.SignalInput("documents", "manual", "doc_synthetic:p1", "v2",
        NOW.isoformat(), ids.content_hmac(KEY, "changed"), "new-encrypted-blob"))
    led.observe_source_versions([sid], complete=True)
    assert led.signal_row(binding.signal_id) is not None
    assert not review.verified(binding)
    led.conn.execute("DELETE FROM signals WHERE signal_id=?", (binding.signal_id,))
    assert not led.conn.execute("SELECT 1 FROM document_field_requirements").fetchone()
    assert not led.conn.execute("SELECT 1 FROM document_field_confirmations").fetchone()
    assert not review.verified(binding)


def test_fact_version_value_and_evidence_must_match(case):
    led, review, binding = case
    original = lg.FactInput("finance", "amount", "123.45", "adult", NOW.isoformat(),
        status=lg.FactStatus.UNKNOWN, evidence=((binding.signal_id, *binding.span),))
    fid = led.put_fact(original)
    binding = replace(binding, fact_id=fid, fact_version=led.get_fact(fid, "owner").recorded_at)
    token = review.preview(binding)["apply_token"]
    review.confirm(binding, token)
    assert review.verified(binding)
    led.put_fact(replace(original, value="999"), fact_id=fid)
    assert not review.verified(binding)
    with pytest.raises(lg.LedgerError):
        review.confirm(binding, token)


@pytest.mark.parametrize("mutation", ["permission", "source"])
def test_independent_process_change_during_review_is_checked_in_winning_transaction(case, mutation):
    led, review, binding = case
    token = review.preview(binding)["apply_token"]
    code = """
import sqlite3, sys
db = sqlite3.connect(sys.argv[1], timeout=10)
db.execute('BEGIN IMMEDIATE')
if sys.argv[2] == 'permission':
    db.execute("UPDATE consent SET revoked_at='2026-10-24T00:00:00Z',revocation_version=revocation_version+1")
else:
    db.execute('UPDATE source_heads SET signal_id=NULL')
db.commit()
"""
    def changed_text(_sid):
        completed = subprocess.run([sys.executable, "-c", code, str(led._path), mutation],
                                   capture_output=True, text=True, timeout=15, check=False,
                                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        assert completed.returncode == 0, completed.stderr
        return TEXT
    racing = FieldReviewStore(led, "owner", lambda: NOW, source_text=changed_text)
    with pytest.raises((ValueError, lg.LedgerError)):
        racing.confirm(binding, token)
    assert not led.conn.execute("SELECT 1 FROM document_field_confirmations").fetchone()


@pytest.mark.parametrize("scope", ["finance", "documents:manual"])
@pytest.mark.parametrize("operations", [["view"], ["correct"]])
def test_both_permissions_are_required_before_reading_text(case, scope, operations):
    led, _, binding = case
    led.conn.execute("UPDATE consent SET operations=? WHERE scope=?", (json.dumps(operations), scope))
    calls = []
    review = FieldReviewStore(led, "owner", lambda: NOW, source_text=lambda sid: calls.append(sid) or TEXT)
    with pytest.raises(lg.PermissionDenied):
        review.preview(binding)
    assert not review.verified(binding)
    assert calls == []


def test_generation_changes_invalidate_token_but_not_content_receipt(case):
    led, review, binding = case
    token = review.preview(binding)["apply_token"]
    grant = led.grant_consent("other", "other", "health", ["view"], "owner")
    led.revoke_consent(grant)
    with pytest.raises(ValueError, match="permissions changed"):
        review.confirm(binding, token)
    review.confirm(binding, review.preview(binding)["apply_token"])
    grant = led.grant_consent("other", "other", "health", ["view"], "owner")
    led.revoke_consent(grant)
    assert review.verified(binding)


def test_forget_removes_receipt_and_tombstone_prevents_reingestion(case):
    led, review, binding = case
    review.confirm(binding, review.preview(binding)["apply_token"])
    led.forget("documents", "manual", "doc_synthetic:p1")
    assert not review.verified(binding)
    assert not led.conn.execute("SELECT 1 FROM document_field_requirements").fetchone()
    assert not led.conn.execute("SELECT 1 FROM document_field_confirmations").fetchone()
    with pytest.raises(lg.Suppressed):
        led.upsert_signal(lg.SignalInput("documents", "manual", "doc_synthetic:p1", "v1",
            NOW.isoformat(), binding.provenance_hmac, "opaque-encrypted-blob"))


@pytest.mark.parametrize("subjects", [[], ["other"], ["unresolved"]])
def test_missing_or_wrong_resolved_subject_provenance_is_denied(case, subjects):
    led, review, binding = case
    led.conn.execute("UPDATE signal_access SET subjects=? WHERE signal_id=?",
                     (json.dumps(subjects), binding.signal_id))
    with pytest.raises(lg.PermissionDenied):
        review.preview(binding)


def test_encryption_and_source_reader_fail_closed(case):
    led, review, binding = case
    review.confirm(binding, review.preview(binding)["apply_token"])
    def unavailable(_sid):
        raise OSError("encrypted blob unavailable")
    broken = FieldReviewStore(led, "owner", lambda: NOW, source_text=unavailable)
    assert not broken.verified(binding)
    for text in (b"not decoded", "x" * 2_000_001):
        broken = FieldReviewStore(led, "owner", lambda: NOW, source_text=lambda _sid: text)
        with pytest.raises(ValueError, match="bounded original text"):
            broken.preview(binding)
    cipher = led._cipher
    led._cipher = None
    with pytest.raises(lg.LedgerError, match="encryption"):
        FieldReviewStore(led, "owner", lambda: NOW, source_text=lambda _sid: TEXT)
    led._cipher = cipher


def test_invalid_span_and_clock_are_rejected(case):
    led, review, binding = case
    with pytest.raises(ValueError, match="span exceeds"):
        review.preview(replace(binding, span=(0, 1000)))
    review = FieldReviewStore(led, "owner", lambda: datetime(2026, 10, 24), source_text=lambda _sid: TEXT)
    with pytest.raises(ValueError, match="explicit instant"):
        review.preview(binding)
    with pytest.raises(ValueError):
        replace(binding, fact_id=True, fact_version="v1")


def test_encrypted_record_tampering_cannot_authorize(case):
    led, review, binding = case
    receipt = review.confirm(binding, review.preview(binding)["apply_token"])
    identity = receipt["binding_id"]
    encrypted, version = CIPHER.encrypt_json({"forged": "123.45"}, aad=identity.encode())
    led.conn.execute("UPDATE document_field_requirements SET payload=?,key_version=?",
                     (encrypted, version))
    assert not review.verified(binding)
    with pytest.raises(lg.LedgerError):
        review.preview(binding)


def test_confirmation_metadata_tampering_cannot_authorize(case):
    led, review, binding = case
    review.confirm(binding, review.preview(binding)["apply_token"])
    led.conn.execute("UPDATE document_field_confirmations SET state='withdrawn'")
    assert not review.verified(binding)
    with pytest.raises(lg.LedgerError):
        review.preview(binding)


def test_literal_json_types_do_not_share_receipts(case):
    _, review, binding = case
    for value in ("1", "1.0", "true", '"1"', "null", '[1]', '{"amount":1}'):
        current = replace(binding, typed_value_json=value)
        assert not review.verified(current)
        review.confirm(current, review.preview(current)["apply_token"])
        assert review.verified(current)
    assert review.verified(replace(binding, typed_value_json='{ "amount" : 1 }'))


def test_receipts_can_be_verified_query_only_and_inside_parent_transaction(case):
    led, review, binding = case
    review.confirm(binding, review.preview(binding)["apply_token"])
    with led.transaction():
        assert review.verified(binding)
    led.conn.execute("PRAGMA query_only=ON")
    try:
        assert review.verified(binding)
        assert not led.conn.in_transaction
    finally:
        led.conn.execute("PRAGMA query_only=OFF")


def test_unconfirmed_query_only_inspection_never_creates_preview_records(case):
    led, review, binding = case
    before = led.conn.total_changes
    led.conn.execute("PRAGMA query_only=ON")
    try:
        assert not review.verified(binding)
        assert not led.conn.in_transaction
        assert not led.conn.execute("SELECT 1 FROM document_field_requirements").fetchone()
        assert not led.conn.execute("SELECT 1 FROM document_field_confirmations").fetchone()
        assert led.conn.total_changes == before
    finally:
        led.conn.execute("PRAGMA query_only=OFF")

def _versions(led, fid):
    return led.conn.execute("SELECT count(*) FROM fact_versions WHERE fact_id=?", (fid,)).fetchone()[0]


def _stored(led, fid):
    return tuple(led.conn.execute("SELECT status,provenance_class FROM facts WHERE fact_id=?", (fid,)).fetchone())


def test_confirming_never_rewrites_the_stored_fact_without_a_version(case):
    """Given an unknown document Fact, when the owner confirms it, then no raw status change happens
    (the verified link makes it current) and the version history is untouched."""
    led, review, binding = case
    fid = led.put_fact(lg.FactInput("finance", "amount", "123.45", "adult", NOW.isoformat(),
        status=lg.FactStatus.UNKNOWN, evidence=((binding.signal_id, *binding.span),)))
    binding = replace(binding, fact_id=fid, fact_version=led.get_fact(fid, "owner").recorded_at)
    before_versions, before_stored = _versions(led, fid), _stored(led, fid)
    review.confirm(binding, review.preview(binding)["apply_token"])
    assert (_versions(led, fid), _stored(led, fid)) == (before_versions, before_stored)
    assert before_stored[0] == "unknown" and review.verified(binding)
    assert led.get_fact(fid, "owner").status == "current"


def test_withdrawing_a_current_fact_records_a_version_through_the_ledger(case):
    """Given a current Fact, when the owner withdraws the review, then it becomes unknown with a history row."""
    led, review, binding = case
    fid = led.put_fact(lg.FactInput("finance", "amount", "123.45", "adult", NOW.isoformat(),
        evidence=((binding.signal_id, *binding.span),)))
    binding = replace(binding, fact_id=fid, fact_version=led.get_fact(fid, "owner").recorded_at)
    before = _versions(led, fid)
    review.withdraw(binding, review.preview(binding)["apply_token"])
    assert _versions(led, fid) == before + 1
    assert _stored(led, fid) == ("unknown", "model_hypothesis")
    latest = led.conn.execute("SELECT status FROM fact_versions WHERE fact_id=? ORDER BY recorded_at DESC",
                              (fid,)).fetchone()[0]
    assert latest == "unknown"
