from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from catchup_core import crypto, ids
from catchup_core import domain_pack as dp
from catchup_core import ledger as lg
from catchup_core.contracts import FactStatus, Provenance
from catchup_core.extract import Candidate
from catchup_core.reconcile import authority_rank, legacy_import, reconcile

PACK = dp.load_pack_file(Path(dp.__file__).parent / "packs" / "kids_school.yaml")
KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x07" * 32}))
PRED = "exam_registration_deadline"      # authority: official_portal_notice > school_email > parent_note


@pytest.fixture
def env(tmp_path):
    ledger = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, owner_id="ved").open()
    ledger.migrate()
    classes, keys = {}, {}

    def signal(upstream, source_class, version="v1", *, observed_at="2026-10-11T00:00:00Z", occurred_at=None):
        sid, _ = ledger.upsert_signal(lg.SignalInput(
            "school", "acct", upstream, version, observed_at, f"h-{upstream}-{version}", f"b:{upstream}",
            occurred_at=occurred_at))
        classes[sid] = source_class
        keys[sid] = ("school", "acct", upstream)
        return sid

    def run(cands):
        return reconcile(ledger, PACK, cands, observed_at="2026-10-11T00:00:00Z",
                         source_class_of=lambda s: classes[s], upstream_key_of=lambda s: keys[s])

    yield ledger, signal, run
    ledger.close()


def cand(sid, value, subject="kid1", predicate=PRED):
    return Candidate(predicate, value, subject, sid, (0, 5), "quote")


def current(ledger, subject="kid1", predicate=PRED):
    return [f for f in ledger.facts_matching("kids_school", predicate, subject, (FactStatus.CURRENT,))]


def test_first_observation_creates_a_current_fact(env):
    ledger, signal, run = env
    r = run([cand(signal("m1", "school_email"), date(2026, 10, 19))])
    assert [c.kind for c in r.changes] == ["created"]
    f = current(ledger)[0]
    assert f.value == "2026-10-19" and f.provenance_class == "verified_source" and f.evidence


@pytest.mark.parametrize("source_class,prior_value", [
    ("school_email", None), ("rumor", None),
    ("school_email", "2026-10-18"), ("official_portal_notice", "2026-10-18"),
])
@pytest.mark.parametrize("occurred_at", [None, "2025-09-01T09:00:00-07:00"])
def test_created_facts_preserve_source_times_and_unknown_validity(env, source_class, prior_value, occurred_at):
    ledger, signal, run = env
    if prior_value:
        run([cand(signal("prior", "school_email"), prior_value)])
    sid, _ = ledger.upsert_signal(lg.SignalInput(
        "school", "acct", "delayed", "v1", "2025-09-02T10:00:00Z", "delayed-hash", "blob",
        occurred_at=occurred_at))
    reconcile(ledger, PACK, [cand(sid, "2026-10-19")], observed_at="2026-10-25T00:00:00Z",
              source_class_of=lambda source: source_class if source == sid else "school_email",
              upstream_key_of=lambda source: ("school", "acct", source))
    row = ledger.conn.execute("SELECT * FROM facts WHERE observed_at=?", ("2025-09-02T10:00:00Z",)).fetchone()
    assert row is not None
    assert row["occurred_at"] == occurred_at
    assert bool(row["validity_unknown"]) is True
    assert row["valid_from"] is None and row["valid_to"] is None
    history = ledger.fact_history(row["fact_id"], "ved")
    assert history[-1]["observed_at"] == row["observed_at"]
    assert history[-1]["occurred_at"] == occurred_at
    assert history[-1]["validity_unknown"] is True


def test_legacy_import_does_not_claim_established_validity(env):
    ledger, _, _ = env
    fact_id = legacy_import(ledger, PACK, {("kid1", PRED): "2026-10-19"},
                            lineage="legacy_state:synthetic@hash", observed_at="2026-10-11T00:00:00Z")[0]
    assert ledger.get_fact(fact_id, "ved").validity_unknown is True
    assert lg.FactInput("kids_school", PRED, "2026-10-19", "kid1", "t").validity_unknown is False


def test_same_value_from_another_source_corroborates(env):
    ledger, signal, run = env
    run([cand(signal("m1", "school_email"), date(2026, 10, 19))])
    r = run([cand(signal("m2", "official_portal_notice"), date(2026, 10, 19))])
    assert [c.kind for c in r.changes] == ["corroborated"]
    assert len(current(ledger)) == 1 and len(current(ledger)[0].evidence) == 2


def test_stronger_authority_supersedes(env):
    ledger, signal, run = env
    run([cand(signal("m1", "school_email"), date(2026, 10, 19))])
    r = run([cand(signal("p1", "official_portal_notice"), date(2026, 10, 22))])
    kinds = sorted(c.kind for c in r.changes)
    assert kinds == ["created", "superseded"]
    assert [f.value for f in current(ledger)] == ["2026-10-22"]


def test_same_upstream_correction_supersedes_even_at_equal_authority(env):
    ledger, signal, run = env
    run([cand(signal("m1", "school_email"), date(2026, 10, 19))])
    r = run([cand(signal("m1", "school_email", version="v2"), date(2026, 10, 21))])
    assert "superseded" in {c.kind for c in r.changes}
    assert [f.value for f in current(ledger)] == ["2026-10-21"]


def test_weaker_source_revision_cannot_supersede_stronger_independent_corroboration(env):
    ledger, signal, run = env
    original = signal("m1", "school_email")
    run([cand(original, date(2026, 10, 19))])
    official = signal("p1", "official_portal_notice")
    run([cand(official, date(2026, 10, 19))])
    fact_id = current(ledger)[0].fact_id

    result = run([cand(signal("m1", "school_email", version="v2"), date(2026, 10, 21))])

    assert {change.kind for change in result.changes} == {"disputed"}
    assert current(ledger) == []
    disputed = ledger.facts_matching("kids_school", PRED, "kid1", (FactStatus.DISPUTED,))
    assert sorted(fact.value for fact in disputed) == ["2026-10-19", "2026-10-21"]
    assert (official, 0, 5) in ledger.get_fact(fact_id, "ved").evidence


def test_equal_authority_conflict_is_a_dispute_not_latest_wins(env):
    ledger, signal, run = env
    run([cand(signal("m1", "school_email"), date(2026, 10, 19))])
    r = run([cand(signal("m2", "school_email"), date(2026, 10, 25))])
    assert {c.kind for c in r.changes} == {"disputed"}
    assert current(ledger) == []
    disputed = ledger.facts_matching("kids_school", PRED, "kid1", (FactStatus.DISPUTED,))
    assert sorted(f.value for f in disputed) == ["2026-10-19", "2026-10-25"]
    assert {f.provenance_class for f in disputed} == {"disputed"}


def test_same_upstream_correction_cannot_supersede_independent_equal_rank_corroboration(env):
    ledger, signal, run = env
    original = signal("m1", "school_email")
    independent = signal("m2", "school_email")
    run([cand(original, date(2026, 10, 19))])
    run([cand(independent, date(2026, 10, 19))])
    fact_id = current(ledger)[0].fact_id
    correction = signal("m1", "school_email", version="v2")

    result = run([cand(correction, date(2026, 10, 21))])

    assert {change.kind for change in result.changes} == {"disputed"}
    assert current(ledger) == []
    disputed = ledger.facts_matching("kids_school", PRED, "kid1", (FactStatus.DISPUTED,))
    assert sorted(fact.value for fact in disputed) == ["2026-10-19", "2026-10-21"]
    assert set(ledger.get_fact(fact_id, "ved").evidence) == {(original, 0, 5), (independent, 0, 5)}
    corrected = next(fact for fact in disputed if fact.value == "2026-10-21")
    assert corrected.evidence == ((correction, 0, 5),)


def test_stronger_matching_source_resolves_dispute_without_duplicating_fact(env):
    ledger, signal, run = env
    original = signal("m1", "school_email", observed_at="2025-09-02T10:00:00Z",
                      occurred_at="2025-09-01T09:00:00-07:00")
    run([cand(original, date(2026, 10, 19))])
    fact_id = current(ledger)[0].fact_id
    competing = signal("m2", "school_email")
    run([cand(competing, date(2026, 10, 21))])
    disputed = ledger.facts_matching("kids_school", PRED, "kid1", (FactStatus.DISPUTED,))
    assert len(disputed) == 2
    competing_id = next(fact.fact_id for fact in disputed if fact.value == "2026-10-21")
    histories = {fact.fact_id: ledger.fact_history(fact.fact_id, "ved") for fact in disputed}
    official = signal("p1", "official_portal_notice")

    result = run([cand(official, date(2026, 10, 19))])

    assert [fact.fact_id for fact in current(ledger)] == [fact_id]
    resolved = ledger.get_fact(fact_id, "ved")
    assert resolved.value == "2026-10-19"
    assert resolved.provenance_class == Provenance.VERIFIED_SOURCE.value
    assert set(resolved.evidence) == {(original, 0, 5), (official, 0, 5)}
    superseded = ledger.get_fact(competing_id, "ved")
    assert superseded.status == FactStatus.SUPERSEDED.value
    assert superseded.evidence == ((competing, 0, 5),)
    assert ledger.facts_matching("kids_school", PRED, "kid1", (FactStatus.DISPUTED,)) == []
    assert result.by_kind("created") == []
    assert [change.fact_id for change in result.by_kind("resolved")] == [fact_id]
    assert ledger.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 2
    for retained_id, before in histories.items():
        after = ledger.fact_history(retained_id, "ved")
        assert after[:len(before)] == before
        for field in ("observed_at", "occurred_at", "valid_from", "valid_to", "validity_unknown"):
            assert after[-1][field] == before[-1][field]


@pytest.mark.parametrize("source_class", ["school_email", "parent_note", "rumor"])
def test_matching_source_without_sufficient_authority_keeps_dispute(env, source_class):
    ledger, signal, run = env
    run([cand(signal("m1", "school_email"), "2026-10-19")])
    run([cand(signal("m2", "school_email"), "2026-10-21")])
    before = ledger.facts_matching("kids_school", PRED, "kid1", (FactStatus.DISPUTED,))

    result = run([cand(signal("matching", source_class), "2026-10-19")])

    after = ledger.facts_matching("kids_school", PRED, "kid1", (FactStatus.DISPUTED,))
    assert {fact.fact_id for fact in after} == {fact.fact_id for fact in before}
    assert current(ledger) == []
    assert result.by_kind("created") == []
    assert result.by_kind("resolved") == []
    assert result.by_kind("superseded") == []


@pytest.mark.parametrize("source_class,expected_status", [
    ("parent_note", FactStatus.DISPUTED),
    ("school_email", FactStatus.DISPUTED),
    ("official_portal_notice", FactStatus.CURRENT),
])
def test_unknown_matching_promotion_checks_conflicting_source_authority(env, source_class, expected_status):
    ledger, signal, run = env
    run([cand(signal("original", "school_email"), "2026-10-19")])
    original_id = current(ledger)[0].fact_id
    run([cand(signal("unverified", "rumor"), "2026-10-21")])
    unknown_id = ledger.facts_matching("kids_school", PRED, "kid1", (FactStatus.UNKNOWN,))[0].fact_id

    result = run([cand(signal("matching", source_class), "2026-10-21")])

    assert ledger.get_fact(unknown_id, "ved").status == expected_status.value
    original_status = FactStatus.SUPERSEDED if expected_status == FactStatus.CURRENT else FactStatus.DISPUTED
    assert ledger.get_fact(original_id, "ved").status == original_status.value
    assert result.by_kind("created") == []
    assert ledger.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 2


@pytest.mark.parametrize("user_confirmed", [False, True])
def test_matching_resolution_requires_authority_over_every_conflicting_fact(env, user_confirmed):
    ledger, signal, run = env
    for upstream, value in (("m1", "2026-10-19"), ("m2", "2026-10-21"), ("m3", "2026-10-22")):
        run([cand(signal(upstream, "school_email"), value)])
    before = ledger.facts_matching("kids_school", PRED, "kid1", (FactStatus.DISPUTED,))
    blocker = next(fact for fact in before if fact.value == "2026-10-22")
    if user_confirmed:
        ledger.set_fact_status(blocker.fact_id, FactStatus.DISPUTED, Provenance.USER_CONFIRMED)
    else:
        ledger.add_evidence(blocker.fact_id, signal("blocker", "official_portal_notice"), 0, 5)

    result = run([cand(signal("matching", "official_portal_notice"), "2026-10-19")])

    after = ledger.facts_matching("kids_school", PRED, "kid1", (FactStatus.DISPUTED,))
    assert {fact.fact_id for fact in after} == {fact.fact_id for fact in before}
    assert current(ledger) == []
    assert result.by_kind("created") == []
    assert result.by_kind("superseded") == []
    if user_confirmed:
        assert ledger.get_fact(blocker.fact_id, "ved").provenance_class == Provenance.USER_CONFIRMED.value


def test_same_upstream_correction_can_outweigh_independent_weaker_corroboration(env):
    ledger, signal, run = env
    original = signal("m1", "school_email")
    independent = signal("n1", "parent_note")
    run([cand(original, "2026-10-19"), cand(independent, "2026-10-19")])
    original_id = current(ledger)[0].fact_id

    result = run([cand(signal("m1", "school_email", version="v2"), "2026-10-21")])

    assert [fact.value for fact in current(ledger)] == ["2026-10-21"]
    assert [change.fact_id for change in result.by_kind("superseded")] == [original_id]
    assert set(ledger.get_fact(original_id, "ved").evidence) == {(original, 0, 5), (independent, 0, 5)}


def test_source_revision_cannot_override_an_independent_equal_authority_conflict(env):
    ledger, signal, run = env
    run([cand(signal("m1", "school_email"), date(2026, 10, 19))])
    run([cand(signal("m2", "school_email"), date(2026, 10, 20))])

    result = run([cand(signal("m1", "school_email", version="v2"), date(2026, 10, 21))])

    assert {change.kind for change in result.changes} == {"disputed"}
    assert current(ledger) == []
    assert len(ledger.facts_matching("kids_school", PRED, "kid1", (FactStatus.DISPUTED,))) == 3


def test_weaker_authority_conflict_is_a_dispute(env):
    ledger, signal, run = env
    run([cand(signal("p1", "official_portal_notice"), date(2026, 10, 19))])
    r = run([cand(signal("n1", "parent_note"), date(2026, 10, 30))])
    assert "disputed" in {c.kind for c in r.changes}
    assert current(ledger) == []


def test_unlisted_source_class_never_overturns(env):
    ledger, signal, run = env
    run([cand(signal("m1", "parent_note"), date(2026, 10, 19))])           # weakest listed
    run([cand(signal("x1", "rumor"), date(2026, 10, 30))])
    assert [fact.value for fact in current(ledger)] == ['2026-10-19']
    unknown = ledger.facts_matching('kids_school', PRED, 'kid1', (FactStatus.UNKNOWN,))
    assert [fact.value for fact in unknown] == ['2026-10-30']
    assert authority_rank(PACK, PRED, "rumor") > authority_rank(PACK, PRED, "parent_note")


def test_user_confirmed_outranks_any_source(env):
    ledger, signal, run = env
    fid = ledger.put_fact(lg.FactInput(domain="kids_school", predicate=PRED, value="2026-10-19",
                                       primary_subject_id="kid1", observed_at="t",
                                       provenance=Provenance.USER_CONFIRMED))
    r = run([cand(signal("p1", "official_portal_notice"), date(2026, 10, 30))])
    assert {c.kind for c in r.changes} == {"disputed"}
    assert ledger.get_fact(fid, "ved").status == "disputed"


def test_user_confirmation_remains_protected_across_successive_source_conflicts(env):
    ledger, signal, run = env
    fact_id = ledger.put_fact(lg.FactInput(
        domain="kids_school", predicate=PRED, value="2026-10-19", primary_subject_id="kid1",
        observed_at="t", provenance=Provenance.USER_CONFIRMED))
    first = run([cand(signal("m1", "school_email"), date(2026, 10, 21))])
    after_first = ledger.get_fact(fact_id, "ved")

    second = run([cand(signal("p1", "official_portal_notice"), date(2026, 10, 22))])

    assert {change.kind for change in first.changes} == {"disputed"}
    assert {change.kind for change in second.changes} == {"disputed"}
    assert after_first.status == FactStatus.DISPUTED.value
    assert after_first.provenance_class == Provenance.USER_CONFIRMED.value
    confirmed = ledger.get_fact(fact_id, "ved")
    assert confirmed.status == FactStatus.DISPUTED.value
    assert confirmed.provenance_class == Provenance.USER_CONFIRMED.value
    assert confirmed.value == "2026-10-19"
    assert current(ledger) == []


def test_same_value_unknown_promotion_cannot_bypass_conflicting_user_confirmation(env):
    ledger, signal, run = env
    confirmed_id = ledger.put_fact(lg.FactInput(
        domain="kids_school", predicate=PRED, value="2026-10-19", primary_subject_id="kid1",
        observed_at="t", provenance=Provenance.USER_CONFIRMED))
    unverified = signal("unverified", "rumor")
    run([cand(unverified, date(2026, 10, 21))])
    unknown = ledger.facts_matching("kids_school", PRED, "kid1", (FactStatus.UNKNOWN,))
    assert len(unknown) == 1
    unknown_id = unknown[0].fact_id
    official = signal("p1", "official_portal_notice")

    result = run([cand(official, date(2026, 10, 21))])

    assert current(ledger) == []
    confirmed = ledger.get_fact(confirmed_id, "ved")
    assert confirmed.status == FactStatus.DISPUTED.value
    assert confirmed.provenance_class == Provenance.USER_CONFIRMED.value
    assert confirmed.value == "2026-10-19"
    promoted = ledger.get_fact(unknown_id, "ved")
    assert promoted.status == FactStatus.DISPUTED.value
    assert promoted.value == "2026-10-21"
    assert set(promoted.evidence) == {(unverified, 0, 5), (official, 0, 5)}
    assert result.by_kind("created") == []


def test_official_document_corroboration_promotes_unknown_same_value_fact(env):
    ledger, signal, run = env
    predicate = "accommodation_status"
    unverified = signal("unverified", "rumor")
    run([cand(unverified, "approved", predicate=predicate)])
    unknown = ledger.facts_matching("kids_school", predicate, "kid1", (FactStatus.UNKNOWN,))
    assert len(unknown) == 1
    assert unknown[0].provenance_class == Provenance.MODEL_HYPOTHESIS.value
    fact_id = unknown[0].fact_id
    document, _ = ledger.upsert_signal(lg.SignalInput(
        "documents", "acct", "official-notice", "v1", "2026-10-11T00:00:00Z",
        "official-notice-hash", "b:official-notice"))
    ledger.set_document_method(document, "text")

    result = reconcile(
        ledger, PACK, [cand(document, "approved", predicate=predicate)],
        observed_at="2026-10-11T00:00:00Z",
        source_class_of=lambda sid: "official_document" if sid == document else "rumor",
        upstream_key_of=lambda sid: ("documents", "acct", "official-notice") if sid == document
        else ("school", "acct", "unverified"))

    assert [change.fact_id for change in result.changes] == [fact_id]
    verified = ledger.get_fact(fact_id, "ved")
    assert verified.status == FactStatus.CURRENT.value
    assert verified.provenance_class == Provenance.VERIFIED_SOURCE.value
    assert verified.value == "approved"
    assert set(verified.evidence) == {(unverified, 0, 5), (document, 0, 5)}
    assert [fact.fact_id for fact in current(ledger, predicate=predicate)] == [fact_id]


@pytest.mark.parametrize("connector,source_class,method", [
    ("school", "rumor", None),
    ("documents", "official_document", "ocr"),
    ("documents", "official_document", None),
])
def test_low_quality_corroboration_keeps_same_value_unknown(env, connector, source_class, method):
    ledger, signal, run = env
    predicate = "accommodation_status"
    original = signal("unverified", "rumor")
    run([cand(original, "approved", predicate=predicate)])
    fact_id = ledger.facts_matching("kids_school", predicate, "kid1", (FactStatus.UNKNOWN,))[0].fact_id
    corroboration, _ = ledger.upsert_signal(lg.SignalInput(
        connector, "acct", "corroboration", "v1", "2026-10-11T00:00:00Z", "corroboration-hash", "b"))
    if method is not None:
        ledger.set_document_method(corroboration, method)

    result = reconcile(
        ledger, PACK, [cand(corroboration, "approved", predicate=predicate)],
        observed_at="2026-10-11T00:00:00Z",
        source_class_of=lambda sid: source_class if sid == corroboration else "rumor",
        upstream_key_of=lambda sid: (connector, "acct", sid))

    assert [(change.kind, change.fact_id) for change in result.changes] == [("corroborated", fact_id)]
    unknown = ledger.get_fact(fact_id, "ved")
    assert unknown.status == FactStatus.UNKNOWN.value
    assert unknown.provenance_class == Provenance.MODEL_HYPOTHESIS.value
    assert set(unknown.evidence) == {(original, 0, 5), (corroboration, 0, 5)}
    assert current(ledger, predicate=predicate) == []


def test_subjects_are_independent(env):
    ledger, signal, run = env
    run([cand(signal("m1", "school_email"), date(2026, 10, 19), "kid1"),
         cand(signal("m1", "school_email"), date(2026, 10, 24), "kid2")])
    assert current(ledger, "kid1")[0].value == "2026-10-19"
    assert current(ledger, "kid2")[0].value == "2026-10-24"


def test_restricted_predicate_values_are_encrypted_at_rest(env):
    ledger, signal, run = env
    run([cand(signal("m1", "official_document"), "approved", predicate="accommodation_status")])
    raw = ledger.conn.execute("SELECT typed_value, value_encrypted FROM facts").fetchone()
    assert raw["value_encrypted"] == 1 and "approved" not in raw["typed_value"]


def test_legacy_claims_are_labelled_rank_last_and_can_be_overturned(env):
    ledger, signal, run = env
    ids_ = legacy_import(ledger, PACK, {("kid1", PRED): date(2026, 10, 19), ("kid1", "unknown_pred"): "x"},
                         lineage="legacy_state:state/kids.md@abc", observed_at="2026-03-22T00:00:00Z")
    assert len(ids_) == 1
    legacy = ledger.get_fact(ids_[0], "ved")
    assert legacy.provenance_class == "legacy_unverified" and legacy.value["lineage"].startswith("legacy_state:")
    # a matching source corroborates the legacy claim rather than duplicating it
    r = run([cand(signal("m1", "parent_note"), date(2026, 10, 19))])
    assert {c.kind for c in r.changes} == {"corroborated"}
    # a differing source, even the weakest listed, outranks legacy
    r = run([cand(signal("m2", "parent_note"), date(2026, 10, 26))])
    assert "superseded" in {c.kind for c in r.changes}
    assert ledger.get_fact(ids_[0], "ved").status == "superseded"
