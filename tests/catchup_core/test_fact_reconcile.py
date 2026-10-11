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

    def signal(upstream, source_class, version="v1"):
        sid, _ = ledger.upsert_signal(lg.SignalInput(
            "school", "acct", upstream, version, "2026-10-11T00:00:00Z", f"h-{upstream}-{version}", f"b:{upstream}"))
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


def test_equal_authority_conflict_is_a_dispute_not_latest_wins(env):
    ledger, signal, run = env
    run([cand(signal("m1", "school_email"), date(2026, 10, 19))])
    r = run([cand(signal("m2", "school_email"), date(2026, 10, 25))])
    assert {c.kind for c in r.changes} == {"disputed"}
    assert current(ledger) == []
    disputed = ledger.facts_matching("kids_school", PRED, "kid1", (FactStatus.DISPUTED,))
    assert sorted(f.value for f in disputed) == ["2026-10-19", "2026-10-25"]
    assert {f.provenance_class for f in disputed} == {"disputed"}


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
    assert current(ledger) == []                                             # disputed, not replaced
    assert authority_rank(PACK, PRED, "rumor") > authority_rank(PACK, PRED, "parent_note")


def test_user_confirmed_outranks_any_source(env):
    ledger, signal, run = env
    fid = ledger.put_fact(lg.FactInput(domain="kids_school", predicate=PRED, value="2026-10-19",
                                       primary_subject_id="kid1", observed_at="t",
                                       provenance=Provenance.USER_CONFIRMED))
    r = run([cand(signal("p1", "official_portal_notice"), date(2026, 10, 30))])
    assert {c.kind for c in r.changes} == {"disputed"}
    assert ledger.get_fact(fid, "ved").status == "disputed"


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
