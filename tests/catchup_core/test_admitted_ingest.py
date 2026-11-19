"""Metadata-first Gmail admission for B.8/E1."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from catchup_core import crypto, ids
from catchup_core import coordinator
from catchup_core import admitted_ingest, ledger as lg
from catchup_core.blobs import EncryptedBlobStore
from catchup_core.contracts import CatchupRequest, CoverageStatus, ExecutionMode, FailureClass
from catchup_core.domain_pack import load_pack_file

KEY = ids.HmacKey(1, b"a" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x12" * 32}))
OWNER = "ved"


class FakeGmail:
    def __init__(self):
        self.metadata = {
            "m1": {
                "id": "m1", "thread_id": "t1", "from": "Billing <billing@pse.com>",
                "to": "owner@example.invalid", "cc": "", "delivered_to": "owner@example.invalid",
                "labels": ["INBOX"], "size": 512, "history_id": "100",
            },
            "foreign": {
                "id": "foreign", "thread_id": "t2", "from": "Sales <sales@foreign.invalid>",
                "to": "owner@example.invalid", "cc": "", "delivered_to": "owner@example.invalid",
                "labels": ["INBOX"], "size": 400, "history_id": "100",
            },
            "work": {
                "id": "work", "thread_id": "t3", "from": "Billing <billing@pse.com>",
                "to": "owner@example.invalid", "cc": "", "delivered_to": "owner@example.invalid",
                "labels": ["Work"], "size": 400, "history_id": "100",
            },
        }
        self.raw_reads: list[str] = []
        self.metadata_reads: list[str] = []
        self.deadline: float | None = None

    def account_identity(self, _auth, *, deadline=None):  # noqa: ARG002
        self.deadline = deadline
        return "owner@example.invalid"

    def list_message_ids(self, _auth, *, query, since, max_results, deadline=None):  # noqa: ARG002
        assert query and since
        assert deadline == self.deadline
        return SimpleNamespace(
            message_ids=list(self.metadata)[:max_results], cursor="100",
            complete=True, truncation_reason=None)

    def message_metadata(self, _auth, message_id, *, deadline=None):  # noqa: ARG002
        assert deadline == self.deadline
        self.metadata_reads.append(message_id)
        return dict(self.metadata[message_id])

    def raw_message(self, _auth, message_id, *, deadline=None):  # noqa: ARG002
        assert deadline == self.deadline
        self.raw_reads.append(message_id)
        return (
            "From: billing@pse.com\r\nTo: owner@example.invalid\r\n"
            "Subject: Utility bill\r\n\r\nPayment due 2026-11-20\r\n"
        ).encode()


@pytest.fixture
def led(tmp_path):
    ledger = lg.Ledger(
        tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER, owner_id=OWNER,
        consent_subjects=frozenset(), known_subjects=frozenset({OWNER})).open()
    ledger.migrate()
    yield ledger
    ledger.close()


SOURCE = {
    "source_name": "home_email",
    "connector": "gmail",
    "account_scope": "personal",
    "query": "from:pse.com",
    "subjects": [OWNER],
    "admission": {
        "allowed_sender_domains": ["pse.com"],
        "work_label_ids": ["Work"],
        "work_recipient_domains": [],
        "unknown_metadata": "hold",
    },
}


def enroll(led, gmail):
    return led.enroll_source(
        reviewer=OWNER, provider_account=gmail.account_identity({}), policy=SOURCE["admission"],
        **{key: SOURCE[key] for key in (
            "source_name", "connector", "account_scope", "query", "subjects")})


@pytest.mark.parametrize(
    ("metadata", "policy", "provider_account", "reason"),
    [
        ({"from": "billing@pse.com"}, SOURCE["admission"], "owner@example.invalid",
         "metadata_ambiguous"),
        ({
            "from": "billing@pse.com", "to": "other@example.invalid", "labels": ["INBOX"],
        }, SOURCE["admission"], "owner@example.invalid", "recipient_not_owner_account"),
        ({
            "from": "billing@pse.com", "to": "owner@example.invalid", "labels": ["INBOX"],
        }, {
            **SOURCE["admission"], "work_recipient_domains": ["example.invalid"],
        }, "owner@example.invalid", "work_recipient"),
        ({
            "from": "billing@pse.com", "to": "owner@example.invalid", "labels": ["INBOX"],
        }, {"allowed_sender_domains": ["pse.com"]}, "owner@example.invalid", "policy_incomplete"),
    ],
)
def test_metadata_classification_refuses_ambiguous_and_unapproved_boundaries(
        metadata, policy, provider_account, reason):
    assert admitted_ingest.classify_metadata(metadata, policy, provider_account) == reason


def test_gmail_record_decoder_preserves_safe_fallbacks_and_plain_text():
    assert admitted_ingest.decode_gmail_record("plain text") == {"text": "plain text"}
    assert admitted_ingest.decode_gmail_record('{"schema":"other"}') == {"text": '{"schema":"other"}'}

    raw = (
        b"From: billing@pse.com\r\n"
        b"To: owner@example.invalid\r\n"
        b"Subject: Utility bill\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/alternative; boundary="x"\r\n\r\n'
        b"--x\r\nContent-Type: text/html\r\n\r\n<p>Bill HTML</p>\r\n"
        b"--x\r\nContent-Type: text/plain\r\n\r\nBill plain text\r\n"
        b"--x--\r\n"
    )
    envelope = admitted_ingest.gmail_raw_record(
        raw, {"id": "m1"}, "marker")
    decoded = admitted_ingest.decode_gmail_record(envelope)
    assert decoded["subject"] == "Utility bill"
    assert "Bill plain text" in decoded["body"]


def test_admitted_fetch_requests_raw_only_for_classification_safe_messages(led):
    gmail = FakeGmail()
    enrollment = enroll(led, gmail)
    result = admitted_ingest.fetch_gmail(
        led, KEY, gmail, {}, source=SOURCE, enrollment=enrollment,
        since="2026-11-01T00:00:00Z", max_results=20)
    assert gmail.raw_reads == ["m1"]
    assert len(result.records) == 1
    assert result.records[0].provider_version == "m1"
    assert result.records[0].raw_content_hmac
    envelope = json.loads(result.records[0].text)
    assert envelope["schema"] == "artha.gmail.raw.v1"
    assert base64.b64decode(envelope["data"])
    assert result.status is CoverageStatus.DEGRADED
    assert {(hold.upstream_id, hold.reason) for hold in result.holds} == {
        ("foreign", "sender_domain_not_enrolled"),
        ("work", "work_label"),
    }
    assert result.listing_complete is True
    assert result.listing_truncation is None


def test_truncated_listing_remains_structured_incomplete_even_when_cursor_exists(led):
    class Truncated(FakeGmail):
        def list_message_ids(self, _auth, *, query, since, max_results, deadline=None):
            assert query and since and deadline == self.deadline
            return SimpleNamespace(
                message_ids=list(self.metadata)[:max_results], cursor="100",
                complete=False, truncation_reason="max_results")

    gmail = Truncated()
    enrollment = enroll(led, gmail)
    result = admitted_ingest.fetch_gmail(
        led, KEY, gmail, {}, source=SOURCE, enrollment=enrollment,
        since="2026-11-01T00:00:00Z", max_results=20)

    assert result.status is CoverageStatus.DEGRADED
    assert result.cursor == "100"
    assert result.listing_complete is False
    assert result.listing_truncation == "max_results"
    assert "listing:max_results" in result.blind_spots


def test_nonstandard_listing_metadata_is_structured_as_unknown(led):
    class LegacyListing(FakeGmail):
        def list_message_ids(self, _auth, *, query, since, max_results, deadline=None):
            return SimpleNamespace(
                message_ids=["m1"], cursor="100", complete=1,
                truncation_reason=42)

    gmail = LegacyListing()
    enrollment = enroll(led, gmail)
    result = admitted_ingest.fetch_gmail(
        led, KEY, gmail, {}, source=SOURCE, enrollment=enrollment,
        since="2026-11-01T00:00:00Z", max_results=1)

    assert result.listing_complete is None
    assert result.listing_truncation is None
    assert result.cursor == "100"


def test_unusable_message_identity_or_raw_payload_is_held_before_admission(led):
    class InvalidPayload(FakeGmail):
        def message_metadata(self, auth, message_id, *, deadline=None):
            value = super().message_metadata(auth, message_id, deadline=deadline)
            if message_id == "m1":
                value["id"] = "different"
            return value

    gmail = InvalidPayload()
    enrollment = enroll(led, gmail)
    result = admitted_ingest.fetch_gmail(
        led, KEY, gmail, {}, source=SOURCE, enrollment=enrollment,
        since="2026-11-01T00:00:00Z", max_results=1)
    assert result.records == ()
    assert result.holds[0].reason == "metadata_identity_changed"
    assert gmail.raw_reads == []


def test_changed_metadata_between_requests_holds_before_raw_use(led):
    class Racing(FakeGmail):
        def __init__(self):
            super().__init__()
            self.metadata["m1"]["labels"] = []
            self.reads = 0

        def message_metadata(self, auth, message_id, *, deadline=None):
            value = super().message_metadata(auth, message_id, deadline=deadline)
            if message_id == "m1":
                self.reads += 1
                if self.reads == 2:
                    value["labels"] = ["Work"]
            return value

    gmail = Racing()
    enrollment = enroll(led, gmail)
    result = admitted_ingest.fetch_gmail(
        led, KEY, gmail, {}, source=SOURCE, enrollment=enrollment,
        since="2026-11-01T00:00:00Z", max_results=20)
    assert result.records == ()
    assert len([hold for hold in result.holds if hold.upstream_id == "m1"]) == 1
    assert "m1" in gmail.raw_reads


def test_oversized_metadata_refuses_raw_payload(led):
    gmail = FakeGmail()
    gmail.metadata["m1"]["size"] = admitted_ingest.MAX_GMAIL_RAW_BYTES + 1
    enrollment = enroll(led, gmail)
    result = admitted_ingest.fetch_gmail(
        led, KEY, gmail, {}, source=SOURCE, enrollment=enrollment,
        since="2026-11-01T00:00:00Z", max_results=1)
    assert gmail.raw_reads == []
    assert result.holds[0].reason == "raw_size_limit"


def test_existing_hold_resumes_by_id_without_waiting_for_a_new_listing(led):
    class MissingFromListing(FakeGmail):
        def list_message_ids(self, auth, *, query, since, max_results):
            return [], "100"

    gmail = MissingFromListing()
    enrollment = enroll(led, gmail)
    led.record_coverage_hold(
        source_name="home_email", connector="gmail", account_scope="personal",
        provider_account_id=enrollment["provider_account_id"], upstream_id="m1",
        upstream_version="m1", detail="prior hold")
    result = admitted_ingest.fetch_gmail(
        led, KEY, gmail, {}, source=SOURCE, enrollment=enrollment,
        since="2026-11-01T00:00:00Z", max_results=20)
    assert gmail.raw_reads == ["m1"]
    assert [record.upstream_id for record in result.records] == ["m1"]


def test_hold_from_old_permission_generation_is_retried_after_reenrollment(led):
    class MissingFromListing(FakeGmail):
        def list_message_ids(self, auth, *, query, since, max_results):
            return [], "100"

    gmail = MissingFromListing()
    first = enroll(led, gmail)
    led.record_coverage_hold(
        source_name="home_email", connector="gmail", account_scope="personal",
        provider_account_id=first["provider_account_id"], upstream_id="m1",
        upstream_version="m1", detail="old generation")
    consent = led.grant_consent("adult_child", "adult_child", "home", ("view",), OWNER)
    led.revoke_consent(consent)
    second = enroll(led, gmail)
    result = admitted_ingest.fetch_gmail(
        led, KEY, gmail, {}, source=SOURCE, enrollment=second,
        since="2026-11-01T00:00:00Z", max_results=20)
    assert [record.upstream_id for record in result.records] == ["m1"]


def test_known_message_content_change_is_held(led):
    gmail = FakeGmail()
    enrollment = enroll(led, gmail)
    sid, _ = led.upsert_signal(lg.SignalInput(
        "home_email", "personal", "m1", "m1", "2026-11-17T00:00:00Z",
        "old-marker", "evd_old"))
    result = admitted_ingest.fetch_gmail(
        led, KEY, gmail, {}, source=SOURCE, enrollment=enrollment,
        since="2026-11-01T00:00:00Z", max_results=20)
    assert result.records == ()
    assert any(hold.upstream_id == "m1" and hold.reason == "content_marker_changed"
               for hold in result.holds)
    assert sid


def test_revocation_after_first_metadata_check_refuses_raw_fetch(led):
    class Revoking(FakeGmail):
        def raw_message(self, auth, message_id):
            raise AssertionError("raw payload must not be requested")

    gmail = Revoking()
    enrollment = enroll(led, gmail)
    original = led.require_source_enrollment

    def revoke_after(*args, **kwargs):
        row = original(*args, **kwargs)
        if not getattr(revoke_after, "done", False):
            led.revoke_source("home_email", reviewer=OWNER)
            revoke_after.done = True
        return row

    led.require_source_enrollment = revoke_after  # type: ignore[method-assign]
    result = admitted_ingest.fetch_gmail(
        led, KEY, gmail, {}, source=SOURCE, enrollment=enrollment,
        since="2026-11-01T00:00:00Z", max_results=20)
    assert result.records == ()
    assert result.status is CoverageStatus.DEGRADED


def test_runtime_build_fetchers_uses_only_the_enrolled_source(led):
    from catchup_core.runtime import RuntimeConfig, build_fetchers

    config = RuntimeConfig(
        {"home_email": {**SOURCE, "subjects": [OWNER]}}, [OWNER], OWNER)
    gmail = FakeGmail()
    enroll(led, gmail)
    fetchers = build_fetchers(
        config, "2026-11-01T00:00:00Z",
        handler_loader=lambda connector: gmail,
        auth_loader=lambda connector: {},
        required={"home_email": True}, ledger=led)
    result = fetchers["home_email"]()
    assert len(result.records) == 1
    assert result.records[0].source_name == "home_email"


def test_runtime_build_fetchers_holds_sibling_gmail_source_before_provider_setup():
    from catchup_core.runtime import RuntimeConfig, build_fetchers

    config = RuntimeConfig({
        "school_email": {
            **SOURCE, "source_name": "school_email",
            "kwargs": {"query": SOURCE["query"]},
        },
    }, [OWNER], OWNER)
    provider_calls = []
    fetchers = build_fetchers(
        config, "2026-11-01T00:00:00Z",
        handler_loader=lambda connector: provider_calls.append(("handler", connector)),
        auth_loader=lambda connector: provider_calls.append(("auth", connector)),
        required={"school_email": True}, ledger=None)

    result = fetchers["school_email"]()
    assert result.status is CoverageStatus.UNAVAILABLE
    assert result.error is FailureClass.PERMISSION
    assert result.records == ()
    assert any("school_email" in detail and "home_email" in detail for detail in result.blind_spots)
    assert provider_calls == []


def test_enrolled_message_flows_signal_fact_and_briefing_without_reclassified_payload(led, tmp_path):
    gmail = FakeGmail()
    enrollment = enroll(led, gmail)
    led.record_coverage_hold(
        source_name="home_email", connector="gmail", account_scope="personal",
        provider_account_id=enrollment["provider_account_id"],
        upstream_id="m1", upstream_version="m1", detail="metadata incomplete")
    result = admitted_ingest.fetch_gmail(
        led, KEY, gmail, {}, source=SOURCE, enrollment=enrollment,
        since="2026-11-01T00:00:00Z", max_results=1)
    blobs = EncryptedBlobStore(tmp_path / "blobs", CIPHER, KEY)
    published = []

    def extract(prompt):
        message = prompt.rsplit("<untrusted>\n", 1)[1].rsplit("\n</untrusted>", 1)[0]
        assert "Payment due 2026-11-20" in message
        start = message.index("Payment due 2026-11-20")
        return json.dumps({
            "complete": True,
            "candidates": [{
                "predicate": "mortgage_due_date",
                "subject_id": OWNER,
                "value": "2026-11-20",
                "quote": "Payment due 2026-11-20",
                "span": [start, start + len("Payment due 2026-11-20")],
            }],
        })

    deps = coordinator.Deps(
        ledger=led, pack=load_pack_file(
            Path(__file__).parents[2] / "scripts" / "catchup_core" / "packs" / "home.yaml"),
        blobs=blobs, id_key=KEY,
        fetchers={"home_email": lambda: result},
        extractor=extract, subjects=[OWNER], viewer=OWNER,
        now=lambda: __import__("datetime").datetime(2026, 11, 18, tzinfo=__import__("datetime").timezone.utc),
        source_class={"home_email": "email_notice"},
        publisher=lambda cid, text, version: published.append((cid, text, version)),
        coverage_line=lambda: "Coverage: synthetic Gmail.")
    fetcher = deps.fetchers["home_email"]
    fetcher.__artha_enrolled_source__ = True  # type: ignore[attr-defined]
    request = CatchupRequest(
        "cu_enrolled", OWNER, "cli", host="test-host", mode=ExecutionMode.AUTHORITATIVE)
    outcome = coordinator.run_catchup(request, deps)
    facts = led.facts_matching("home", "mortgage_due_date", OWNER)
    assert outcome.artifact_ids and published, (
        outcome.reason_unpublished,
        [dict(row) for row in led.conn.execute(
            "SELECT signal_id,processing_state,exclusion_reason FROM signals").fetchall()],
        [dict(row) for row in led.conn.execute(
            "SELECT signal_id,stage,error_class FROM signal_failures").fetchall()],
        [dict(row) for row in led.conn.execute(
            "SELECT object_type,object_id,scope,provenance FROM scope_bindings").fetchall()],
        [(row["fact_id"], led._fact_reviewed(led.conn, row["fact_id"]))
         for row in led.conn.execute("SELECT fact_id FROM facts").fetchall()],
        [(
            row["recorded_at"],
            led._has_reviewed_binding(
                "fact_version", row["fact_id"], row["recorded_at"], "ledger", OWNER,
                row["scope_hmac"], db=led.conn)
        ) for row in led.conn.execute(
            "SELECT fact_id,recorded_at,scope_hmac FROM fact_versions").fetchall()])
    assert len(facts) == 1 and facts[0].evidence
    assert led.conn.execute(
        "SELECT COUNT(*) FROM signal_source_enrollments").fetchone()[0] == 1
    assert led.coverage_holds("home_email")[0]["state"] == "admitted"
