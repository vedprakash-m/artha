"""GAP-PAID: a later "paid" fact for the same named obligation resolves the bill (PR-01..PR-07)."""
from __future__ import annotations

from datetime import timedelta

import pytest
import yaml

from catchup_core import coordinator as co
from catchup_core import domain_pack as dp
from catchup_core import ledger as lg
from catchup_core.contracts import Provenance
from catchup_core.triage import triage

from test_triage import KEY, CIPHER, NOW, build_pack, conn_of, fresh  # noqa: F401

BILL = "exam_registration_deadline"
PAID = "exam_registration_paid"
OBLIGATION = "ob1"


def paid_pack(resolves=BILL):
    path = dp.__file__.replace("domain_pack.py", "packs/kids_school.yaml")
    data = yaml.safe_load(open(path))
    data["predicates"].append({"name": PAID, "type": "enum", "values": ["paid"],
                               "authority": ["school_email"], "resolves": resolves})
    return dp.load_pack(data)


@pytest.fixture
def env(tmp_path):
    ledger = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, owner_id="ved").open()
    ledger.migrate()
    yield ledger
    ledger.close()


def put(ledger, predicate, value, tag, obligation=OBLIGATION, subject="kid1"):
    sid, _ = ledger.upsert_signal(lg.SignalInput("school_email", "acct", f"u-{tag}", "v1", "t", f"h-{tag}", "b"))
    ledger.put_fact(lg.FactInput(
        domain="kids_school", predicate=predicate, value=value, primary_subject_id=subject, observed_at="t",
        provenance=Provenance.VERIFIED_SOURCE, evidence=((sid, 0, 4),), obligation_key=obligation))
    return sid


def run(ledger, seen, pack=None):
    """``seen`` maps signal id to when its evidence was observed."""
    return triage(ledger, pack or paid_pack(), NOW, viewer="ved", connector_of=conn_of(ledger),
                  freshness={**fresh(), **seen})


def test_pack_declares_what_a_predicate_resolves():
    assert paid_pack().predicates[PAID].resolves == BILL                                              # PR-01


def test_resolves_must_name_another_predicate_of_the_pack():
    with pytest.raises(dp.PackError, match="resolves"):
        paid_pack(resolves="no_such_predicate")                                                       # PR-02
    with pytest.raises(dp.PackError, match="resolves"):
        paid_pack(resolves=PAID)


def test_a_later_paid_fact_resolves_the_open_bill(env):
    bill = put(env, BILL, "2026-10-14", "bill")
    paid = put(env, PAID, "paid", "paid")
    seen = {bill: NOW - timedelta(hours=30), paid: NOW - timedelta(hours=2)}
    assert run(env, seen).alerts == () and run(env, seen).horizon == ()                              # PR-03


def test_a_paid_fact_older_than_the_bill_does_not_resolve_it(env):
    bill = put(env, BILL, "2026-10-14", "bill")
    paid = put(env, PAID, "paid", "paid")
    seen = {bill: NOW - timedelta(hours=2), paid: NOW - timedelta(hours=30)}                         # next month's bill
    assert [a.deadline.isoformat() for a in run(env, seen).alerts] == ["2026-10-14"]                  # PR-04


def test_paid_for_a_different_obligation_resolves_nothing(env):
    bill = put(env, BILL, "2026-10-14", "bill")
    paid = put(env, PAID, "paid", "paid", obligation="ob2")
    seen = {bill: NOW - timedelta(hours=30), paid: NOW - timedelta(hours=2)}
    assert len(run(env, seen).alerts) == 1                                                            # PR-05


def test_unknown_evidence_times_never_hide_a_bill(env):
    put(env, BILL, "2026-10-14", "bill")
    put(env, PAID, "paid", "paid")
    pack = paid_pack()
    result = triage(env, pack, NOW, viewer="ved", connector_of=conn_of(env), freshness={})
    assert all(a.state == "unknown_stale" for a in result.alerts) and result.alerts                  # PR-06


def test_paid_fact_of_another_subject_resolves_nothing(env):
    bill = put(env, BILL, "2026-10-14", "bill")
    paid = put(env, PAID, "paid", "paid", subject="kid2")
    seen = {bill: NOW - timedelta(hours=30), paid: NOW - timedelta(hours=2)}
    assert len(run(env, seen).alerts) == 1                                                            # PR-07


def test_the_link_key_ignores_the_predicate_a_paid_fact_resolves():
    pack = paid_pack()
    assert co.link_predicate(pack, PAID) == BILL and co.link_predicate(pack, BILL) == BILL           # PR-08


def test_the_finance_pack_settles_a_bill_the_biller_says_is_paid(env):                               # PR-09
    path = dp.__file__.replace("domain_pack.py", "packs/finance.yaml")
    pack = dp.load_pack(yaml.safe_load(open(path)))
    assert pack.predicates["payment_status"].resolves == "bill_due_date"
    ids = {}
    for predicate, value in (("bill_due_date", "2026-10-14"), ("payment_status", "paid")):
        sid, _ = env.upsert_signal(lg.SignalInput("finance_email", "acct", f"u-{predicate}", "v1", "t",
                                                  f"h-{predicate}", "b"))
        env.put_fact(lg.FactInput(domain="finance", predicate=predicate, value=value, primary_subject_id="ved",
                                  observed_at="t", evidence=((sid, 0, 4),), obligation_key="hospital"))
        ids[predicate] = sid
    seen = {"finance_email": NOW, ids["bill_due_date"]: NOW - timedelta(hours=30), ids["payment_status"]: NOW}
    result = triage(env, pack, NOW, viewer="ved", connector_of=conn_of(env), freshness=seen)
    assert result.alerts == () and result.horizon == ()


def _finance_pack():
    path = dp.__file__.replace("domain_pack.py", "packs/finance.yaml")
    return dp.load_pack(yaml.safe_load(open(path)))


def test_a_received_payment_is_grounded_by_the_words_a_biller_uses():                                  # PR-10
    from catchup_core.extract import supports_value
    pred = _finance_pack().predicates["payment_status"]
    assert supports_value(pred, "received", "We received your payment of $35.00 on September 26.")
    assert supports_value(pred, "received", "Payment received - thank you.")
    assert not supports_value(pred, "received", "We have not received your payment.")
    assert not supports_value(pred, "received", "If we received your payment, ignore this notice.")


def test_a_received_payment_settles_the_bill_like_a_paid_one(env):                                   # PR-11
    pack = _finance_pack()
    assert "received" in pack.predicates["payment_status"].values
    ids = {}
    for predicate, value in (("bill_due_date", "2026-10-01"), ("payment_status", "received")):
        sid, _ = env.upsert_signal(lg.SignalInput("finance_email", "acct", f"u-{predicate}", "v1", "t",
                                                  f"h-{predicate}", "b"))
        env.put_fact(lg.FactInput(domain="finance", predicate=predicate, value=value, primary_subject_id="ved",
                                  observed_at="t", evidence=((sid, 0, 4),), obligation_key="discover"))
        ids[predicate] = sid
    seen = {"finance_email": NOW, ids["bill_due_date"]: NOW - timedelta(hours=60), ids["payment_status"]: NOW - timedelta(hours=30)}
    result = triage(env, pack, NOW, viewer="ved", connector_of=conn_of(env), freshness=seen)
    assert result.alerts == () and result.horizon == ()


def test_any_posted_payment_grounds_received_whatever_the_amount():                                    # PR-12 (owner rule)
    from catchup_core.extract import supports_value
    pred = _finance_pack().predicates["payment_status"]
    assert "amount not compared" in pred.hint
    assert supports_value(pred, "received", "Your Payment of $10.00 posted to your account on September 26, 2026. We've received your payment")
