"""Synthetic regressions for corpus provenance and retained-output access."""
import json
from datetime import UTC, datetime

import pytest
from test_coordinator import CIPHER, KEY

from catchup_core import corpus, ledger as lg

NOW = datetime(2026, 10, 21, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    with lg.Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER,
                   consent_subjects=frozenset({"adult"}),
                   known_subjects=frozenset({"owner", "adult"})).open() as led:
        led.migrate()
        yield led


def source(led, upstream="u1", subject="owner"):
    sid, _ = led.upsert_signal(lg.SignalInput("mail", "private", upstream, "v1",
                                            NOW.isoformat(), upstream, "blob"))
    led.set_signal_subjects(sid, [subject])
    return sid


def case(sid):
    return {"case_id": "case_a", "domain": "kids_school", "signal_id": sid, "excerpt": "Synthetic"}


def replace_label(led, label):
    path = led._path.parent / "corpus" / "case_a.ae1"
    path.write_bytes(CIPHER.encrypt(json.dumps(label).encode(), aad=b"case_a")[0])


def test_labeling_does_not_attest_live_source_provenance(store):
    label = corpus.record_label(store, case(source(store)), {"needs_alert": "no"}, NOW)
    assert "consented_live" not in label
    replace_label(store, dict(label, consented_live=True))
    assert "consented_live" not in corpus.load_labels(store)[0]


def test_duplicate_expected_obligations_are_refused_instead_of_deduplicated():
    with pytest.raises(corpus.CorpusError, match="distinct"):
        corpus.validate({"needs_alert": "yes", "severity": "P1", "expected": ["exam", "exam"]})


@pytest.mark.parametrize("changes", [
    {"reviewed": "false"},
    {"source_negative_reviewed": 1},
    {"reviewed": False},
    {"needs_alert": "maybe"},
    {"expected": ["unexpected"]},
    {"needs_alert": "unsure", "reviewed": True},
])
def test_malformed_retained_labels_cannot_become_reviewed_evidence(store, changes):
    label = corpus.record_label(store, case(source(store)), {"needs_alert": "no"}, NOW)
    replace_label(store, dict(label, **changes))
    with pytest.raises(corpus.CorpusError):
        corpus.load_labels(store)


def test_label_payload_cannot_rebind_its_registered_source(store):
    label = corpus.record_label(store, case(source(store)), {"needs_alert": "no"}, NOW)
    replace_label(store, dict(label, signal_id=source(store, "u2")))
    with pytest.raises(corpus.CorpusError, match="source"):
        corpus.load_labels(store)


def test_unregistered_label_cannot_establish_review_evidence(store):
    label = corpus.record_label(store, case(source(store)), {"needs_alert": "no"}, NOW)
    store.conn.execute("DELETE FROM corpus_cases WHERE case_id=?", ("case_a",))
    replace_label(store, label)
    with pytest.raises(corpus.CorpusError, match="source"):
        corpus.load_labels(store)


def retain_outcome(led, sid, provider_signal_generations=None):
    outcome = {"catchup_id": "cu_test", "signals": [sid],
               "alerts": [{"obligation_key": "private_obligation", "signals": [sid]}],
               "permission_generation": led._permission_generation(),
               "coverage_complete": True, "quiet_claim": False, "citations_resolve": True,
               "versions": {}, "cohort": {}}
    if provider_signal_generations is not None:
        outcome["provider_signal_generations"] = provider_signal_generations
    corpus.record_outcome(led, "cu_test", outcome, NOW)
    led.conn.execute("INSERT INTO stage_journal(catchup_id,stage,status,input_versions,artifact_ids,updated_at) "
                     "VALUES(?,?,?,?,?,?)", ("cu_test", "PRESENTED", "committed", "{}", "[]", NOW.isoformat()))
    return outcome


def test_retained_outcome_rechecks_source_access_before_disclosure(store):
    sid = source(store, subject="adult")
    grant = store.grant_consent("adult", "adult", "mail:private", ["view"], "owner")
    expected = retain_outcome(store, sid)
    assert corpus.latest_outcome(store, "cu_test") == {
        **expected, "consented_live_signal_ids": []}
    store.revoke_consent(grant)
    with pytest.raises(lg.PermissionDenied):
        corpus.latest_outcome(store, "cu_test")


def test_added_subject_cannot_bypass_retained_outcome_access(store):
    sid = source(store)
    retain_outcome(store, sid)
    store.set_signal_subjects(sid, ["adult"])
    with pytest.raises(lg.PermissionDenied):
        corpus.latest_outcome(store, "cu_test")


def test_provider_fetch_provenance_is_bound_to_the_active_permission_generation(store):
    sid = source(store, subject="adult")
    grant = store.grant_consent("adult", "adult", "mail:private", ["view"], "owner")
    generation = store._permission_generation()
    retain_outcome(store, sid, {sid: generation})
    outcome = corpus.latest_outcome(store, "cu_test")
    label = corpus.record_label(store, case(sid), {"needs_alert": "no"}, NOW)
    assert outcome["consented_live_signal_ids"] == [sid]
    assert corpus.evaluations([label], outcome)[0]["consented_live"] is True

    store.revoke_consent(grant)
    store.grant_consent("adult", "adult", "mail:private", ["view"], "owner")
    refreshed = corpus.latest_outcome(store, "cu_test")
    assert refreshed["consented_live_signal_ids"] == []
    assert corpus.evaluations([label], refreshed)[0]["consented_live"] is False


def test_legacy_outcomes_and_unbound_provider_signals_are_not_live(store):
    sid = source(store)
    retain_outcome(store, sid)
    legacy = corpus.latest_outcome(store, "cu_test")
    label = corpus.record_label(store, case(sid), {"needs_alert": "no"}, NOW)
    assert legacy["consented_live_signal_ids"] == []
    assert corpus.evaluations([label], legacy)[0]["consented_live"] is False


def test_provider_provenance_cannot_name_a_signal_outside_the_frozen_outcome(store):
    sid = source(store)
    other = source(store, "u2")
    retain_outcome(store, sid, {other: store._permission_generation()})
    with pytest.raises(corpus.CorpusError, match="provider provenance"):
        corpus.latest_outcome(store, "cu_test")
