"""LAPSE (replay finding): a date nobody can still act on stops being surfaced after ``lapse_days`` (LP-01..LP-05)."""
from __future__ import annotations

import pytest
import yaml

from catchup_core import domain_pack as dp
from catchup_core import ledger as lg
from catchup_core.triage import triage

from test_triage import KEY, CIPHER, NOW, conn_of, fresh  # noqa: F401

SIGNUP = "signup_deadline"


def pack(lapse=None):
    data = yaml.safe_load(open(dp.__file__.replace("domain_pack.py", "packs/kids_school.yaml")))
    for predicate in data["predicates"]:
        if predicate["name"] == SIGNUP:
            predicate.pop("lapse_days", None)
            if lapse is not None:
                predicate["lapse_days"] = lapse
    return dp.load_pack(data)


@pytest.fixture
def env(tmp_path):
    ledger = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, owner_id="ved").open()
    ledger.migrate()
    yield ledger
    ledger.close()


def put(ledger, value):
    sid, _ = ledger.upsert_signal(lg.SignalInput("school_email", "acct", "u1", "v1", "t", "h1", "b"))
    ledger.put_fact(lg.FactInput(domain="kids_school", predicate=SIGNUP, value=value, primary_subject_id="kid1",
                                 observed_at="t", evidence=((sid, 0, 4),)))


def alerts(ledger, p):
    return triage(ledger, p, NOW, viewer="ved", connector_of=conn_of(ledger), freshness={}).alerts   # unverified: the shape the replay surfaced


def test_pack_reads_lapse_days():
    assert pack(0).predicates[SIGNUP].lapse_days == 0 and pack().predicates[SIGNUP].lapse_days is None   # LP-01


def test_lapse_days_must_be_a_small_non_negative_integer():
    for bad in (-1, 4000, "soon"):
        with pytest.raises(dp.PackError):
            pack(bad)                                                                                    # LP-02


def test_a_lapsed_deadline_is_no_longer_overdue(env):
    put(env, "2026-09-09")                       # a month before NOW
    assert alerts(env, pack(0)) == ()                                                                    # LP-03


def test_without_lapse_days_an_old_deadline_stays_overdue(env):
    put(env, "2026-09-09")
    assert len(alerts(env, pack())) == 1                                                                 # LP-04


def test_a_deadline_inside_the_grace_window_still_shows(env):
    put(env, "2026-10-09")                       # three days before NOW
    assert len(alerts(env, pack(7))) == 1 and alerts(env, pack(2)) == ()                                 # LP-05
