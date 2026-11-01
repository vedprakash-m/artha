"""PAY-WORDING: biller confirmation wording settles a bill; failed or future payments do not (PW-01..PW-09)."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from catchup_core import domain_pack as dp
from catchup_core import extract as ex
from catchup_core import ledger as lg
from catchup_core.extract import supports_value
from catchup_core.triage import triage

from test_triage import KEY, CIPHER, NOW, conn_of  # noqa: F401

FINANCE = Path(dp.__file__).parent / "packs" / "finance.yaml"


def finance(unless=None):
    data = yaml.safe_load(FINANCE.read_text(encoding="utf-8"))
    if unless is not None:
        for pred in data["predicates"]:
            if pred["name"] == "payment_status":
                pred["unless"] = unless
    return dp.load_pack(data)


def payment_status():
    return finance().predicates["payment_status"]


@pytest.mark.parametrize("sentence", [
    "Your payment posted to your account. Thank you.",
    "Payment posted: $42.00 to Example Utility.",
    "Your payment of $42.00 is being processed.",
    "ACH Success Payment - Example Utility account ending 0000.",
    "Payment successful. Thank you for paying Example Utility.",
])
def test_confirmation_wording_grounds_a_settled_payment(sentence):                                    # PW-01
    assert supports_value(payment_status(), "received", sentence)


@pytest.mark.parametrize("sentence", [
    "Your payment failed.",
    "Your payment of $42.00 was declined.",
    "Your payment posted on October 1 has been returned.",
    "ACH payment returned: insufficient funds.",
    "Your scheduled payment will be posted on November 3.",
    "Your payment is pending and is being processed.",
    "Payment unsuccessful. Please update your card.",
    "Your payment posted but was reversed.",
])
def test_failed_or_future_payment_wording_never_grounds_a_settled_payment(sentence):                  # PW-02
    pred = payment_status()
    assert not supports_value(pred, "received", sentence)
    assert not supports_value(pred, "paid", sentence)


def test_blocking_words_apply_only_to_the_predicate_that_declares_them():                              # PW-03
    autopay = finance().predicates["autopay_status"]
    assert autopay.unless == ()
    assert supports_value(autopay, "failed", "Your autopay payment failed.")


def test_unless_loads_as_words_and_defaults_to_none():                                                 # PW-04
    assert "failed" in payment_status().unless and "pending" in payment_status().unless
    assert finance(["late"]).predicates["payment_status"].unless == ("late",)


@pytest.mark.parametrize("unless", ["failed", [], [3], [" "], {"failed": True}])
def test_malformed_unless_is_rejected(unless):                                                         # PW-05
    with pytest.raises(dp.PackError, match="unless"):
        finance(unless)


def test_unless_is_only_for_enum_predicates():                                                         # PW-06
    data = yaml.safe_load(FINANCE.read_text(encoding="utf-8"))
    for pred in data["predicates"]:
        if pred["name"] == "statement_balance":
            pred["unless"] = ["failed"]
    with pytest.raises(dp.PackError, match="unless"):
        dp.load_pack(data)


@pytest.fixture
def env(tmp_path):
    ledger = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, owner_id="ved").open()
    ledger.migrate()
    yield ledger
    ledger.close()


def _extract(pack, text, value):
    signal = ex.NormalizedSignal("sig_pay", "finance_email", text, source_class="email_notice",
                                 observed_at="2026-10-12T00:00:00Z")
    quote = text.split(".")[0]
    item = {"predicate": "payment_status", "value": value, "subject_id": "ved", "quote": quote,
            "span": [0, len(quote)], "obligation_ref": "exampleutility"}
    return ex.parse_output(json.dumps({"complete": True, "candidates": [item]}), pack, signal, ["ved"])


def _triage_after(env, pack, payment_value):
    ids = {}
    for predicate, value in (("bill_due_date", "2026-10-01"), ("payment_status", payment_value)):
        sid, _ = env.upsert_signal(lg.SignalInput("finance_email", "acct", f"u-{predicate}", "v1", "t",
                                                  f"h-{predicate}", "b"))
        env.put_fact(lg.FactInput(domain="finance", predicate=predicate, value=value, primary_subject_id="ved",
                                  observed_at="t", evidence=((sid, 0, 4),), obligation_key="exampleutility"))
        ids[predicate] = sid
    seen = {"finance_email": NOW, ids["bill_due_date"]: NOW - timedelta(hours=60),
            ids["payment_status"]: NOW - timedelta(hours=30)}
    return triage(env, pack, NOW, viewer="ved", connector_of=conn_of(env), freshness=seen)


def test_a_paid_autopay_bill_no_longer_raises_the_overdue_alert(env):                                  # PW-07
    pack = finance()
    result = _extract(pack, "ACH Success Payment of $42.00 to Example Utility. Autopay is on.", "received")
    assert result.ok and not result.rejections and len(result.candidates) == 1
    outcome = _triage_after(env, pack, result.candidates[0].value)
    assert outcome.alerts == () and outcome.horizon == ()


def test_a_returned_autopay_payment_is_rejected_and_the_bill_stays_overdue(env):                       # PW-08
    pack = finance()
    result = _extract(pack, "Your autopay payment posted on October 1 has been returned. Call us.", "received")
    assert result.candidates == () and "does not support" in result.rejections[0].reason
    bill, _ = env.upsert_signal(lg.SignalInput("finance_email", "acct", "u-bill", "v1", "t", "h-bill", "b"))
    env.put_fact(lg.FactInput(domain="finance", predicate="bill_due_date", value="2026-10-01",
                              primary_subject_id="ved", observed_at="t", evidence=((bill, 0, 4),),
                              obligation_key="exampleutility"))
    outcome = triage(env, pack, NOW, viewer="ved", connector_of=conn_of(env),
                     freshness={"finance_email": NOW, bill: NOW - timedelta(hours=60)})
    assert [a.severity for a in outcome.alerts] == ["P1"]


def test_the_finance_pack_version_records_the_wording_change():                                       # PW-09
    assert finance().version == "0.7.0"
