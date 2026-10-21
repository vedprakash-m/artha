"""RA-10: a flag or a passing test is not 'exercised' or 'measured'."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from catchup_core.capabilities import runtime_states

REPO = Path(__file__).resolve().parents[2]


def journal(finalized: bool, failures: bool = False) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE stage_journal(catchup_id, stage, status)")
    db.execute("CREATE TABLE signal_failures(signal_id)")
    db.execute("CREATE TABLE capability_evidence(seq INTEGER PRIMARY KEY,capability,state)")
    if finalized:
        db.execute("INSERT INTO stage_journal VALUES('c','FINALIZED','committed')")
        for cap in ("RA-01", "RA-03", "RA-16"):
            state = "degraded" if failures and cap != "RA-03" else "last succeeded"
            db.execute("INSERT INTO capability_evidence(capability,state) VALUES(?,?)", (cap,state))
    if failures:
        db.execute("INSERT INTO signal_failures VALUES('s')")
    return db


def states(db, *, domains=("kids_school",), measured=False):
    cfg = {"domains": list(domains), "reviewed_capabilities": ["RA-01"] if measured else []} if domains else {}
    return {c["id"]: c for c in runtime_states(REPO, cfg, db, measured, extractor_configured=True)}


def test_nothing_configured_or_never_run_is_not_reported_as_working():
    assert states(journal(False), domains=())["RA-01"]["state"] == "not configured"
    assert states(journal(False))["RA-01"]["state"] == "unknown"      # configured but never exercised


def test_exercised_is_last_succeeded_and_measured_needs_release_evidence():
    ok = states(journal(True))
    assert ok["RA-01"] == {"id": "RA-01", "state": "last succeeded", "measured": "no"}
    assert states(journal(True), measured=True)["RA-01"]["measured"] == "yes"
    assert ok["RA-05"]["state"] == "unknown"
    assert states(journal(True), measured=True)["RA-11"]["measured"] == "no"


def test_failures_degrade_coverage_capabilities_and_missing_code_is_not_implemented():
    degraded = states(journal(True, failures=True))
    assert degraded["RA-01"]["state"] == "degraded" and degraded["RA-16"]["state"] == "degraded"
    assert degraded["RA-03"]["state"] == "last succeeded"
    assert degraded["RA-19"]["state"] == "disabled" and degraded["RA-20"]["state"] == "disabled"


def test_a_broken_or_absent_ledger_degrades_to_unknown_not_a_crash():
    assert states(None)["RA-01"]["state"] == "unknown"
    broken = sqlite3.connect(":memory:")                             # no tables
    assert states(broken)["RA-01"]["state"] == "unknown"


def test_a_capability_whose_code_is_missing_is_not_implemented(tmp_path):
    from catchup_core.capabilities import runtime_states
    everything = runtime_states(tmp_path, {"domains": ["kids_school"]}, journal(True), False, extractor_configured=True)
    assert {c["state"] for c in everything} == {"not implemented"}
