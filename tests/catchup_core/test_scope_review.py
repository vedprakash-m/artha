"""Owner scope review with authorization-bound (v2) proofs — specs/refix-artha.md §B.1 A-1, R-1..R-6.

These tests run without the synthetic admission fixture: they exercise the production hard stops.
"""
from __future__ import annotations

import sqlite3

import pytest

from catchup_core import crypto, ids
from catchup_core import ledger as lg
from catchup_core.contracts import Confidentiality

KEY = ids.HmacKey(1, b"r" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x05" * 32}))
OWNER = "ved"


@pytest.fixture
def led(tmp_path):
    state = {"n": 0}

    def tick() -> str:
        state["n"] += 1
        return f"2026-11-18T00:00:{state['n']:02d}Z"

    ledger = lg.Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER, owner_id=OWNER,
                       consent_subjects=frozenset({"adult_child"}), clock=tick).open()
    ledger.migrate()
    yield ledger
    ledger.close()


def signal(led, upstream="m1", subjects=(OWNER,)):
    sid, _ = led.upsert_signal(lg.SignalInput(
        "home_email", "acct", upstream, "v1", "2026-11-17T00:00:00Z", f"h-{upstream}",
        f"blob:{upstream}", Confidentiality.ORDINARY))
    led.set_signal_subjects(sid, list(subjects))
    led.set_source_subjects("home_email", "acct", [OWNER])
    return sid


def fact(led, evidence, **kw):
    base = dict(domain="home", predicate="hvac_service_due", value={"date": "2026-12-01"},
                primary_subject_id=OWNER, observed_at="2026-11-17T00:00:00Z",
                confidentiality=Confidentiality.ORDINARY, evidence=tuple((s, 0, 4) for s in evidence))
    base.update(kw)
    return lg.FactInput(**base)


def review(led, object_type, object_id, scope="personal", reviewer=OWNER):
    item = led.scope_review_item(object_type, object_id)
    return led.review_scope(object_type, object_id, scope, reviewer=reviewer,
                            expected_version=item["object_version"], expected_authz=item["authz_digest"])


# ── hard stops are unchanged without a v2 review ──────────────────────────

def test_unreviewed_signal_stays_behind_the_hard_stop(led):
    sid = signal(led)
    with pytest.raises(lg.PersonalScopeUnavailable):
        led.require_personal_signal(sid)


def test_v1_source_review_binding_is_still_held(led):
    sid = signal(led)
    row = led.signal_row(sid)
    with led.transaction() as db:
        led._record_personal_binding(db, "signal", sid, row["upstream_version"], row["connector"],
                                     row["account_scope"], row["content_hmac"], provenance="source_review")
    with pytest.raises(lg.PersonalScopeUnavailable):
        led.require_personal_signal(sid)


# ── Signals ───────────────────────────────────────────────────────────────

def test_owner_review_admits_signal(led):
    sid = signal(led)
    review(led, "signal", sid)
    assert led.require_personal_signal(sid)["signal_id"] == sid


@pytest.mark.parametrize("scope", ["work", "mixed", "unknown"])
def test_nonpersonal_review_denies_signal(led, scope):
    sid = signal(led)
    review(led, "signal", sid, scope)
    with pytest.raises(lg.PermissionDenied):
        led.require_personal_signal(sid)


def test_changed_signal_subjects_after_review_deny(led):
    sid = signal(led)
    review(led, "signal", sid)
    led.set_signal_subjects(sid, ["adult_child"])
    with pytest.raises(lg.PermissionDenied):
        led.require_personal_signal(sid)


def test_changed_source_access_after_review_denies(led):
    sid = signal(led)
    review(led, "signal", sid)
    led.set_source_subjects("home_email", "acct", [OWNER, "adult_child"])
    with pytest.raises(lg.PermissionDenied):
        led.require_personal_signal(sid)


def test_tombstoned_reviewed_signal_is_denied(led):
    sid = signal(led)
    review(led, "signal", sid)
    row = led.signal_row(sid)
    led.conn.execute("INSERT INTO tombstones(tombstone_id,connector,account_scope,upstream_id,content_hmac,"
                     "created_at,reason) VALUES('tmb_x',?,?,?,?,'2026-11-18T00:00:00Z','test')",
                     (row["connector"], row["account_scope"], row["upstream_id"], row["content_hmac"]))
    with pytest.raises(lg.PermissionDenied):
        led.require_personal_signal(sid)


def test_forged_v2_row_with_wrong_proof_is_denied(led):
    sid = signal(led)
    review(led, "signal", sid)
    led.conn.execute("UPDATE scope_bindings SET proof='0'||substr(proof,2) WHERE object_id=?", (sid,))
    with pytest.raises(lg.PermissionDenied):
        led.require_personal_signal(sid)


def test_duplicate_binding_rows_deny(led):
    sid = signal(led)
    review(led, "signal", sid)
    led.conn.execute("INSERT INTO scope_bindings(object_type,object_id,object_version,source_id,account_id,"
                     "content_hmac,scope,provenance,permission_generation,proof,created_at) "
                     "SELECT object_type,object_id,object_version,source_id,account_id,content_hmac,scope,"
                     "provenance,permission_generation,proof,created_at FROM scope_bindings WHERE object_id=?",
                     (sid,))
    with pytest.raises(lg.PermissionDenied):
        led.require_personal_signal(sid)


def test_signal_operation_admits_reviewed_signal(led):
    sid = signal(led)
    review(led, "signal", sid)
    led.require_signal_operation(sid, OWNER)


def test_signal_operation_without_review_stays_held(led):
    sid = signal(led)
    with pytest.raises(lg.PersonalScopeUnavailable):
        led.require_signal_operation(sid, OWNER)


# ── review preconditions and audit ────────────────────────────────────────

def test_non_owner_review_is_refused(led):
    sid = signal(led)
    with pytest.raises(lg.PermissionDenied):
        review(led, "signal", sid, reviewer="spouse")
    assert led.conn.execute("SELECT COUNT(*) FROM scope_bindings").fetchone()[0] == 0


def test_review_refuses_changed_authorization_inputs(led):
    sid = signal(led)
    item = led.scope_review_item("signal", sid)
    led.set_signal_subjects(sid, ["adult_child"])
    with pytest.raises(lg.LedgerError, match="changed since review"):
        led.review_scope("signal", sid, "personal", reviewer=OWNER,
                         expected_version=item["object_version"], expected_authz=item["authz_digest"])


def test_review_refuses_changed_version(led):
    sid = signal(led)
    item = led.scope_review_item("signal", sid)
    with pytest.raises(lg.LedgerError, match="changed since review"):
        led.review_scope("signal", sid, "personal", reviewer=OWNER,
                         expected_version="other", expected_authz=item["authz_digest"])


def test_invalid_scope_or_object_type_is_refused(led):
    sid = signal(led)
    item = led.scope_review_item("signal", sid)
    with pytest.raises(ValueError, match="scope must be"):
        led.review_scope("signal", sid, "everything", reviewer=OWNER,
                         expected_version=item["object_version"], expected_authz=item["authz_digest"])
    with pytest.raises(ValueError, match="Signals and Facts only"):
        led.scope_review_item("curated", "goals")
    with pytest.raises(lg.LedgerError):
        led.scope_review_item("signal", "sig_missing")


def test_replacement_review_is_audited_and_denies(led):
    sid = signal(led)
    review(led, "signal", sid)
    review(led, "signal", sid, "work")
    with pytest.raises(lg.PermissionDenied):
        led.require_personal_signal(sid)
    audit = led.conn.execute("SELECT object_id,old_scope,new_scope,reviewer FROM scope_reviews "
                             "ORDER BY review_id").fetchall()
    assert [tuple(r) for r in audit] == [(sid, None, "personal", OWNER), (sid, "personal", "work", OWNER)]
    assert led.conn.execute("SELECT COUNT(*) FROM scope_bindings WHERE object_id=?", (sid,)).fetchone()[0] == 1
    review(led, "signal", sid)
    assert led.require_personal_signal(sid)["signal_id"] == sid


def test_scope_review_audit_is_append_only(led):
    sid = signal(led)
    review(led, "signal", sid)
    with pytest.raises(sqlite3.DatabaseError):
        led.conn.execute("UPDATE scope_reviews SET new_scope='work'")
    with pytest.raises(sqlite3.DatabaseError):
        led.conn.execute("DELETE FROM scope_reviews")


def test_review_item_lists_authorization_inputs(led):
    sid = signal(led)
    item = led.scope_review_item("signal", sid)
    assert item["authz"]["subjects"] == [OWNER]
    assert item["authz"]["source_subjects"] == [OWNER]
    assert item["current_scope"] is None
    review(led, "signal", sid)
    assert led.scope_review_item("signal", sid)["current_scope"] == "personal"


# ── Facts ─────────────────────────────────────────────────────────────────

def test_reviewed_fact_with_reviewed_evidence_is_readable(led):
    sid = signal(led)
    fid = led.put_fact(fact(led, [sid]))
    review(led, "signal", sid)
    review(led, "fact", fid)
    assert led.get_fact(fid, OWNER).value == {"date": "2026-12-01"}


def test_reviewed_fact_with_unreviewed_evidence_is_denied(led):
    a, b = signal(led, "a"), signal(led, "b")
    fid = led.put_fact(fact(led, [a, b]))
    review(led, "signal", a)
    review(led, "fact", fid)
    with pytest.raises(lg.PermissionDenied):
        led.get_fact(fid, OWNER)


def test_fact_without_review_stays_held(led):
    sid = signal(led)
    fid = led.put_fact(fact(led, [sid]))
    review(led, "signal", sid)
    with pytest.raises(lg.PersonalScopeUnavailable):
        led.get_fact(fid, OWNER)


@pytest.mark.parametrize("change", ["viewer", "subject"])
def test_changed_fact_authorization_inputs_deny(led, change):
    sid = signal(led)
    fid = led.put_fact(fact(led, [sid]))
    review(led, "signal", sid)
    review(led, "fact", fid)
    if change == "viewer":
        led.conn.execute("INSERT INTO fact_viewers(fact_id,viewer_id) VALUES(?,'spouse')", (fid,))
    else:
        led.conn.execute("INSERT INTO fact_subjects(fact_id,subject_id,role) VALUES(?,'adult_child','x')", (fid,))
    with pytest.raises(lg.PermissionDenied):
        led.get_fact(fid, OWNER)


def test_new_version_with_unchanged_inputs_carries_review_forward(led):
    sid = signal(led)
    fid = led.put_fact(fact(led, [sid]))
    review(led, "signal", sid)
    review(led, "fact", fid)
    led.put_fact(fact(led, [sid], value={"date": "2026-12-05"}), fact_id=fid)
    assert led.get_fact(fid, OWNER).value == {"date": "2026-12-05"}


def test_new_version_with_new_subject_is_denied(led):
    sid = signal(led)
    fid = led.put_fact(fact(led, [sid]))
    review(led, "signal", sid)
    review(led, "fact", fid)
    led.put_fact(fact(led, [sid], co_subjects=(("adult_child", "x"),)), fact_id=fid)
    with pytest.raises(lg.PermissionDenied):
        led.get_fact(fid, OWNER)


def test_new_version_with_unreviewed_new_evidence_is_denied(led):
    a, b = signal(led, "a"), signal(led, "b")
    fid = led.put_fact(fact(led, [a]))
    review(led, "signal", a)
    review(led, "fact", fid)
    led.put_fact(fact(led, [a, b]), fact_id=fid)
    with pytest.raises(lg.PermissionDenied):
        led.get_fact(fid, OWNER)


def test_update_of_unreviewed_fact_stays_held(led):
    sid = signal(led)
    fid = led.put_fact(fact(led, [sid]))
    with pytest.raises(lg.PersonalScopeUnavailable):
        led.put_fact(fact(led, [sid], value={"date": "2026-12-05"}), fact_id=fid)


# ── reviewed migrations never auto-apply to an existing ledger (R-6) ──────

@pytest.mark.parametrize("schema", [19, 20])
def test_existing_ledger_below_reviewed_migration_refuses(schema):
    from types import SimpleNamespace

    from catchup_core import runtime
    with pytest.raises(runtime.RuntimeConfigError, match="reviewed Personal scope migration"):
        runtime._require_personal_scope_migration_review(SimpleNamespace(schema_version=lambda: schema))


def test_current_ledger_passes_reviewed_migration_check():
    from types import SimpleNamespace

    from catchup_core import runtime
    latest = lg.Ledger.available_migrations()[-1][0]
    runtime._require_personal_scope_migration_review(SimpleNamespace(schema_version=lambda: latest))


# ── permission-generation refresh ─────────────────────────────────────────

def test_unknown_fact_cannot_be_reviewed(led):
    with pytest.raises(lg.LedgerError, match="unknown or unbound Fact"):
        led.scope_review_item("fact", "fct_missing")


def test_authorization_inputs_refuse_unknown_objects(led):
    sid = signal(led)
    fid = led.put_fact(fact(led, [sid]))
    with pytest.raises(lg.LedgerError, match="unknown object"):
        lg.Ledger._authz_inputs(led.conn, "signal", "sig_missing", "v1")
    with pytest.raises(lg.LedgerError, match="unknown object"):
        lg.Ledger._authz_inputs(led.conn, "curated", fid, "v1")


def test_refresh_drops_review_whose_object_was_purged(led):
    sid = signal(led)
    consent = led.grant_consent("adult_child", "adult_child", "home", ("view",), OWNER)
    review(led, "signal", sid)
    led.conn.execute("DELETE FROM signals WHERE signal_id=?", (sid,))
    led.revoke_consent(consent)
    assert led.conn.execute("SELECT COUNT(*) FROM scope_bindings WHERE permission_generation=?",
                            (led._permission_generation(),)).fetchone()[0] == 0


def test_refresh_carries_unchanged_v2_review_forward(led):
    sid = signal(led)
    consent = led.grant_consent("adult_child", "adult_child", "home", ("view",), OWNER)
    review(led, "signal", sid)
    led.revoke_consent(consent)
    assert led.require_personal_signal(sid)["signal_id"] == sid


def test_refresh_drops_v2_review_with_changed_inputs(led):
    sid = signal(led)
    consent = led.grant_consent("adult_child", "adult_child", "home", ("view",), OWNER)
    review(led, "signal", sid)
    led.set_signal_subjects(sid, ["adult_child"])
    led.revoke_consent(consent)
    generation = led._permission_generation()
    assert led.conn.execute("SELECT COUNT(*) FROM scope_bindings WHERE object_id=? AND permission_generation=?",
                            (sid, generation)).fetchone()[0] == 0
    with pytest.raises(lg.PermissionDenied):
        led.require_personal_signal(sid)


# ── phase 2: collection reads filter per object ───────────────────────────

def reviewed_fact(led, upstream="m1", **kw):
    sid = signal(led, upstream)
    review(led, "signal", sid)
    fid = led.put_fact(fact(led, [sid], **kw))
    review(led, "fact", fid)
    return sid, fid


def unreviewed_fact(led, upstream="m9", **kw):
    sid = signal(led, upstream)
    return sid, led.put_fact(fact(led, [sid], **kw))


def test_facts_for_returns_reviewed_facts_and_counts_the_rest(led):
    _, keep = reviewed_fact(led)
    unreviewed_fact(led)
    before = led.scope_withheld["facts_for"]
    views = led.facts_for("home", "hvac_service_due", OWNER)
    assert [v.fact_id for v in views] == [keep]
    assert led.scope_withheld["facts_for"] - before == 1


def test_withheld_facts_are_never_decoded(led, monkeypatch):
    unreviewed_fact(led)
    decoded = []
    monkeypatch.setattr(led, "_decode_value", lambda *a: decoded.append(a))
    with pytest.raises(lg.PersonalScopeUnavailable):
        led.facts_for("home", "hvac_service_due", OWNER)
    assert decoded == []


def test_beliefs_lists_only_reviewed_facts(led):
    from catchup_core import views
    _, keep = reviewed_fact(led)
    unreviewed_fact(led)
    before = led.scope_withheld["beliefs"]
    assert [b["fact_id"] for b in views.beliefs(led, OWNER)] == [keep]
    assert led.scope_withheld["beliefs"] - before == 1


def test_interchange_reports_scope_withheld_separately(led):
    from catchup_core import interchange
    _, keep = reviewed_fact(led)
    unreviewed_fact(led)
    document = interchange.build_interchange(led, OWNER)
    assert [f["fact_id"] for f in document["facts"]] == [keep]
    assert document["scope_withheld"] == 1


def test_write_interchange_reports_scope_withheld(led, tmp_path):
    from catchup_core import interchange
    reviewed_fact(led)
    unreviewed_fact(led)
    result = interchange.write_interchange(tmp_path / "out.bin", led, OWNER)
    assert (result["exported_facts"], result["scope_withheld"]) == (1, 1)


def test_emergency_view_with_only_held_facts_is_unavailable_not_empty(led):
    from catchup_core import interchange
    unreviewed_fact(led)
    with pytest.raises(lg.PersonalScopeUnavailable):
        interchange.emergency_view(led, "custodian")


def test_emergency_view_reports_held_facts_beside_visible_ones(led):
    from catchup_core import interchange
    _, keep = reviewed_fact(led)
    unreviewed_fact(led)
    view = interchange.emergency_view(led, OWNER)
    assert [f["fact_id"] for f in view["facts"]] == [keep] and view["scope_withheld"] == 1


def test_signals_in_state_with_only_held_signals_is_unavailable(led):
    sid = signal(led, "m1")
    with pytest.raises(lg.PersonalScopeUnavailable, match="Signal"):
        led.signals_in_state(lg.ProcessingState(led.signal_row(sid)["processing_state"]))


def test_beliefs_command_shows_the_held_count_as_a_gap(led):
    import argparse

    from catchup_core import commands
    reviewed_fact(led)
    unreviewed_fact(led)

    class Deps:
        ledger, viewer = led, OWNER

        def validate_inputs(self):
            return None

    result = commands.execute(argparse.Namespace(command="beliefs", domain=None, subject=None), Deps(),
                              led._path.parent)
    assert result["scope_withheld"] == 1 and "scope-review" in result["gaps"][0]


def test_beliefs_domain_filter_does_not_count_held_facts_elsewhere(led):
    from catchup_core import views
    _, keep = reviewed_fact(led)
    unreviewed_fact(led, domain="finance")
    before = led.scope_withheld["beliefs"]
    assert [b["fact_id"] for b in views.beliefs(led, OWNER, domain="home")] == [keep]
    assert led.scope_withheld["beliefs"] == before


def test_facts_matching_admits_when_every_match_is_reviewed(led):
    _, fid = reviewed_fact(led)
    assert [f.fact_id for f in led.facts_matching("home", "hvac_service_due", OWNER)] == [fid]


def test_facts_matching_fails_closed_on_any_unreviewed_match(led):
    reviewed_fact(led)
    unreviewed_fact(led)
    with pytest.raises(lg.PersonalScopeUnavailable):
        led.facts_matching("home", "hvac_service_due", OWNER)


def test_signals_in_state_filters_unreviewed_signals(led):
    keep = signal(led, "m1")
    review(led, "signal", keep)
    signal(led, "m2")
    state = lg.ProcessingState(led.signal_row(keep)["processing_state"])
    before = led.scope_withheld["signals_in_state"]
    assert [r["signal_id"] for r in led.signals_in_state(state)] == [keep]
    assert led.scope_withheld["signals_in_state"] - before == 1


def test_candidates_need_a_reviewed_signal(led):
    keep = signal(led, "m1")
    review(led, "signal", keep)
    held = signal(led, "m2")
    led.put_candidates(keep, [{"x": 1}])
    led.put_candidates(held, [{"x": 2}])
    assert led.candidates_for(keep) == [{"x": 1}]
    with pytest.raises(lg.PersonalScopeUnavailable):
        led.candidates_for(held)


def test_completion_fact_reads_a_reviewed_confirmation(led):
    from catchup_core import curated
    key = "open_items:abc"
    _, fid = reviewed_fact(led, predicate="commitment_completed", obligation_key=key,
                           provenance=lg.Provenance.USER_CONFIRMED,
                           value={"record_key": key, "completed": True})
    assert curated.completion_fact(led, OWNER, None, key).fact_id == fid


def test_completion_fact_stays_held_when_unreviewed(led):
    from catchup_core import curated
    key = "open_items:abc"
    unreviewed_fact(led, predicate="commitment_completed", obligation_key=key,
                    provenance=lg.Provenance.USER_CONFIRMED, value={"record_key": key, "completed": True})
    with pytest.raises(lg.PersonalScopeUnavailable):
        curated.completion_fact(led, OWNER, None, key)


def _inventory(led):
    from types import SimpleNamespace

    from catchup_core.retention import RetentionPlanner
    planner = SimpleNamespace(ledger=led, max_rows=100000, max_bytes=10 ** 9, _describe=RetentionPlanner._describe)
    return RetentionPlanner._tables(planner)


def test_retention_inventory_stays_held_even_when_every_object_is_reviewed(led):
    # The inventory reads whole tables (calendar, candidates, drafts) that A-1 keeps held.
    reviewed_fact(led)
    with pytest.raises(lg.PersonalScopeUnavailable):
        _inventory(led)


def test_status_change_of_reviewed_fact_carries_review_forward(led):
    _, fid = reviewed_fact(led)
    led.set_fact_status(fid, lg.FactStatus.DISPUTED)
    assert led.get_fact(fid, OWNER).status == "disputed"


def test_added_reviewed_evidence_carries_review_forward(led):
    _, fid = reviewed_fact(led)
    extra = signal(led, "m2")
    review(led, "signal", extra)
    led.add_evidence(fid, extra, 0, 4)
    assert len(led.get_fact(fid, OWNER).evidence) == 2


def test_added_unreviewed_evidence_denies_the_new_version(led):
    _, fid = reviewed_fact(led)
    extra = signal(led, "m2")
    led.add_evidence(fid, extra, 0, 4)
    with pytest.raises(lg.PermissionDenied):
        led.get_fact(fid, OWNER)


@pytest.mark.parametrize("write", ["status", "evidence"])
def test_writes_to_unreviewed_facts_stay_held(led, write):
    sid, fid = unreviewed_fact(led)
    if write == "status":
        with pytest.raises(lg.PersonalScopeUnavailable):
            led.set_fact_status(fid, lg.FactStatus.DISPUTED)
    else:
        with pytest.raises(lg.PersonalScopeUnavailable):
            led.add_evidence(fid, sid, 1, 2)


def test_ingest_paths_stay_held_after_review(led):
    from catchup_core import heldout
    sid, _ = reviewed_fact(led)
    with pytest.raises(lg.PersonalScopeUnavailable):
        led.replace_source_support(sid)
    with pytest.raises(lg.PersonalScopeUnavailable):
        led.cancel_calendar("home_email", "acct", "m1")
    with pytest.raises(lg.PersonalScopeUnavailable):
        heldout._facts(led, sid, OWNER)
