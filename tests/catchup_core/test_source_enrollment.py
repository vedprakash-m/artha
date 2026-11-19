"""Source enrollment, coverage holds and admitted Signal binding for E1."""
from __future__ import annotations

import pytest

from catchup_core import crypto, ids
from catchup_core import ledger as lg
from catchup_core import runtime
from catchup_core.contracts import CatchupRequest, Confidentiality, ExecutionMode

KEY = ids.HmacKey(1, b"e" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x11" * 32}))
OWNER = "ved"


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        self.store[(service, name)] = value


@pytest.fixture
def led(tmp_path):
    ledger = lg.Ledger(
        tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER, owner_id=OWNER,
        consent_subjects=frozenset(), known_subjects=frozenset({OWNER}),
        clock=lambda: "2026-11-18T13:00:00Z").open()
    ledger.migrate()
    yield ledger
    ledger.close()


def definition(**change):
    value = {
        "source_name": "home_email",
        "connector": "gmail",
        "provider_account": "owner@example.invalid",
        "account_scope": "personal",
        "query": 'from:(pse.com) subject:(bill OR payment)',
        "subjects": [OWNER],
        "policy": {
            "allowed_sender_domains": ["pse.com"],
            "work_label_ids": [],
            "work_recipient_domains": [],
        },
    }
    value.update(change)
    return value


def add_signal(led, content_hmac="raw-hmac-1", upstream="message-1", version="message-1"):
    sid, _ = led.upsert_signal(lg.SignalInput(
        "gmail", "personal", upstream, version, "2026-11-18T13:00:00Z",
        content_hmac, "evd_existing", Confidentiality.SENSITIVE))
    led.set_signal_subjects(sid, [OWNER])
    led.set_source_subjects("gmail", "personal", [OWNER])
    return sid


def test_source_enrollment_is_append_only_and_query_bound(led):
    item = led.enroll_source(reviewer=OWNER, **definition())
    assert item["action"] == "enroll"
    assert item["provider_account_id"] != definition()["provider_account"]
    assert len(led.source_enrollments("home_email")) == 1
    enrollment = led.require_source_enrollment(**definition())
    assert enrollment["enrollment_id"] == item["enrollment_id"]
    with pytest.raises(lg.PersonalScopeUnavailable):
        led.require_source_enrollment(**definition(query="new query"))
    with pytest.raises(Exception, match="append-only"):
        led.conn.execute("DELETE FROM source_enrollments")


def test_revoke_holds_existing_enrollment_immediately(led):
    item = led.enroll_source(reviewer=OWNER, **definition())
    led.revoke_source("home_email", reviewer=OWNER)
    with pytest.raises(lg.PersonalScopeUnavailable):
        led.require_source_enrollment(**definition())
    rows = led.source_enrollments("home_email")
    assert [row["action"] for row in rows] == ["enroll", "revoke"]
    assert rows[-1]["enrollment_id"] != item["enrollment_id"]


def test_enrollment_review_requires_owner_and_resolved_subjects(led):
    with pytest.raises(lg.PermissionDenied):
        led.enroll_source(reviewer="guest", **definition())
    with pytest.raises(lg.LedgerError):
        led.enroll_source(reviewer=OWNER, **definition(subjects=["unknown"]))


@pytest.mark.parametrize("source_name", ["school_email", "work_email"])
def test_source_enrollment_refuses_sibling_gmail_sources(led, source_name):
    with pytest.raises(lg.PermissionDenied, match="approved.*home_email"):
        led.enroll_source(reviewer=OWNER, **definition(source_name=source_name))
    assert led.source_enrollments(source_name) == []


def test_enrolled_signal_gets_v2_binding_linked_to_enrollment(led):
    enrollment = led.enroll_source(reviewer=OWNER, **definition())
    sid = add_signal(led)
    led.bind_enrolled_signal(
        sid, source_name="home_email", enrollment_id=enrollment["enrollment_id"],
        metadata_marker="metadata-1")
    row = led.conn.execute(
        "SELECT enrollment_id,metadata_marker FROM signal_source_enrollments WHERE signal_id=?", (sid,)
    ).fetchone()
    assert dict(row) == {
        "enrollment_id": enrollment["enrollment_id"], "metadata_marker": "metadata-1"}
    item = led.scope_review_item("signal", sid)
    assert item["authz"]["enrollment_id"] == enrollment["enrollment_id"]
    assert led.require_personal_signal(sid)["signal_id"] == sid


def test_enrolled_signal_refuses_wrong_or_revoked_enrollment(led):
    enrollment = led.enroll_source(reviewer=OWNER, **definition())
    sid = add_signal(led)
    with pytest.raises(lg.PersonalScopeUnavailable):
        led.bind_enrolled_signal(sid, source_name="home_email", enrollment_id=999,
                                 metadata_marker="metadata-1")
    led.bind_enrolled_signal(
        sid, source_name="home_email", enrollment_id=enrollment["enrollment_id"],
        metadata_marker="metadata-1")
    led.revoke_source("home_email", reviewer=OWNER)
    with pytest.raises(lg.PermissionDenied):
        led.require_personal_signal(sid)


def test_current_source_expectation_holds_stale_query_or_account(led):
    enrollment = led.enroll_source(reviewer=OWNER, **definition())
    sid = add_signal(led)
    led.bind_enrolled_signal(
        sid, source_name="home_email", enrollment_id=enrollment["enrollment_id"],
        metadata_marker="metadata-1")
    led.expect_source_enrollment(
        source_name="home_email", connector="gmail", account_scope="personal",
        query="a changed query", subjects=[OWNER], policy=definition()["policy"],
        provider_account="owner@example.invalid")
    with pytest.raises(lg.PermissionDenied):
        led.require_personal_signal(sid)


@pytest.mark.parametrize("change", [
    {"kwargs": {"query": "a changed query"}},
    {"admission": {
        "allowed_sender_domains": ["changed.invalid"],
        "work_label_ids": [],
        "work_recipient_domains": [],
    }},
    {"provider_account": "other@example.invalid"},
    {"account_scope": "family"},
    {"subjects": ["someone-else"]},
    {"admission": None},
])
def test_runtime_reopen_revalidates_source_definition_without_manual_expectation(tmp_path, change):
    local = tmp_path / "runtime-local"
    source = {
        "connector": "gmail",
        "provider_account": definition()["provider_account"],
        "account_scope": "personal",
        "kwargs": {"query": definition()["query"]},
        "admission": definition()["policy"],
    }
    config = runtime.RuntimeConfig({"home_email": source}, [OWNER], OWNER)
    request = CatchupRequest("cu_enrollment_reopen", OWNER, "cli", mode=ExecutionMode.SHADOW)
    backend = FakeKeyring()
    ledger, _ = runtime._open_ledger(local, config, request, CIPHER, KEY, backend)
    enrollment = ledger.enroll_source(reviewer=OWNER, **definition())
    sid = add_signal(ledger)
    ledger.bind_enrolled_signal(
        sid, source_name="home_email", enrollment_id=enrollment["enrollment_id"],
        metadata_marker="metadata-1")
    fact_id = ledger.put_fact(lg.FactInput(
        domain="home", predicate="bill_due", value={"date": "2026-12-01"},
        primary_subject_id=OWNER, observed_at="2026-11-18T13:00:00Z",
        confidentiality=Confidentiality.SENSITIVE, evidence=((sid, 0, 4),)))
    ledger.close()

    changed = {**source, **change}
    if change == {"admission": None}:
        changed.pop("admission")
    reopened, _ = runtime._open_ledger(
        local,
        runtime.RuntimeConfig({"home_email": changed}, [OWNER], OWNER),
        request, CIPHER, KEY, backend, read_only=True)
    try:
        for read in (
                lambda: reopened.require_personal_signal(sid),
                lambda: reopened.get_fact(fact_id, OWNER),
                lambda: reopened.fact_history(fact_id, OWNER)):
            with pytest.raises(lg.PermissionDenied):
                read()
    finally:
        reopened.close()


def test_new_fact_from_enrolled_signals_gets_audited_v2_binding(led):
    enrollment = led.enroll_source(reviewer=OWNER, **definition())
    sid = add_signal(led)
    led.bind_enrolled_signal(
        sid, source_name="home_email", enrollment_id=enrollment["enrollment_id"],
        metadata_marker="metadata-1")
    fid = led.put_fact(lg.FactInput(
        domain="home", predicate="bill_due", value={"date": "2026-12-01"},
        primary_subject_id=OWNER, observed_at="2026-11-18T13:00:00Z",
        confidentiality=Confidentiality.SENSITIVE, evidence=((sid, 0, 4),)))
    assert led.get_fact(fid, OWNER).fact_id == fid
    rows = led.conn.execute(
        "SELECT object_type,reviewer FROM scope_reviews WHERE reviewer='system:new_fact' "
        "ORDER BY review_id").fetchall()
    assert [row["object_type"] for row in rows] == ["fact", "fact_version"]


def test_privacy_history_restore_path_invalidates_scope_authority(led):
    from catchup_core.backup import apply_privacy_history
    enrollment = led.enroll_source(reviewer=OWNER, **definition())
    sid = add_signal(led)
    led.bind_enrolled_signal(
        sid, source_name="home_email", enrollment_id=enrollment["enrollment_id"],
        metadata_marker="metadata-1")
    checkpoint = led.seal_privacy()
    apply_privacy_history(
        led.conn, lambda _name: None, checkpoint, led.privacy_history())
    assert led.conn.execute(
        "SELECT COUNT(*) FROM scope_bindings WHERE provenance='source_review'").fetchone()[0] == 0
    assert led.conn.execute("SELECT COUNT(*) FROM signal_source_enrollments").fetchone()[0] == 0
    with pytest.raises(lg.PersonalScopeUnavailable):
        led.require_active_source_enrollment("home_email")


def test_coverage_hold_is_idempotent_and_bounded(led):
    led.record_coverage_hold(
        source_name="home_email", connector="gmail", account_scope="personal",
        provider_account_id="acct-hmac", upstream_id="m1", upstream_version="m1",
        detail="classification metadata changed")
    led.record_coverage_hold(
        source_name="home_email", connector="gmail", account_scope="personal",
        provider_account_id="acct-hmac", upstream_id="m1", upstream_version="m1",
        detail="second observation")
    rows = led.coverage_holds("home_email")
    assert len(rows) == 1
    assert rows[0]["detail"] == "second observation"
    led.resolve_coverage_hold("home_email", "acct-hmac", "m1", "m1", "excluded")
    assert led.coverage_holds("home_email", state="held") == []
