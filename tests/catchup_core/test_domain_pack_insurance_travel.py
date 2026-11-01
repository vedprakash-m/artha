"""Insurance and travel Domain packs run through the unchanged core (RA-14, specs/features/packs-insurance-travel.md)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from catchup_core import crypto, ids
from catchup_core import domain_pack as dp
from catchup_core import ledger as lg
from catchup_core.contracts import Severity
from catchup_core.extract import supports_value
from catchup_core.triage import triage

PACKS = Path(dp.__file__).parent / "packs"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x07" * 32}))
NOW = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)      # 2026-10-12 local


def load(domain):
    return dp.load_pack_file(PACKS / f"{domain}.yaml")


@pytest.fixture
def ledger(tmp_path):
    led = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, owner_id="ved").open()
    led.migrate()
    yield led
    led.close()


def put(led, domain, predicate, value, subject="ved", obligation=None):
    tag = f"{subject}-{predicate}-{value}-{obligation}"
    sid, _ = led.upsert_signal(lg.SignalInput(f"{domain}_email", "acct", f"u-{tag}", "v1", "t", f"h-{tag}", "b"))
    extra = {"obligation_key": obligation} if obligation else {}
    led.put_fact(lg.FactInput(domain=domain, predicate=predicate, value=value, primary_subject_id=subject,
                              observed_at="t", evidence=((sid, 0, 4),), **extra))
    return sid


def run(led, pack, seen=None):
    def connector_of(sid):
        return led.conn.execute("SELECT connector FROM signals WHERE signal_id=?", (sid,)).fetchone()[0]
    return triage(led, pack, NOW, viewer="ved", connector_of=connector_of,
                  freshness={f"{pack.domain}_email": NOW, **(seen or {})})


def severity_of(led, pack, predicate, value):
    put(led, pack.domain, predicate, value)
    alerts = [a for a in run(led, pack).alerts if a.predicate == predicate]
    return alerts[0].severity if alerts else None


# ── AC1 load ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("domain,predicates", [
    ("insurance", {"policy_renewal_date", "premium_due_date", "premium_payment_status", "claim_status"}),
    ("travel", {"departure_date", "hotel_checkin_date", "document_expiry_date"}),
])
def test_given_the_new_pack_when_loaded_then_it_is_strict_and_declares_what_it_lacks(domain, predicates):
    pack = load(domain)
    assert pack.domain == domain and pack.version == "0.1.0"
    assert set(pack.predicates) == predicates
    assert [(s.connector, s.required, s.freshness_hours) for s in pack.sources] == [(f"{domain}_email", True, 48)]
    assert pack.retention is None and "retention" in pack.extension_requirements
    assert pack.fixtures_dir == f"tests/fixtures/{domain}/"


def test_given_every_shipped_pack_when_composed_then_predicate_ownership_is_unambiguous():
    packs = [dp.load_pack_file(p) for p in sorted(PACKS.glob("*.yaml"))]
    both = dp.composite_pack(packs)
    assert both.predicate_domains["premium_payment_status"] == "insurance"
    assert both.predicate_domains["document_expiry_date"] == "travel"
    assert both.predicate_domains["payment_status"] == "finance"


# ── AC2 grounding against synthetic fixtures ──────────────────────────────

def fixture(domain, message_id):
    lines = (FIXTURES / domain / "emails.jsonl").read_text(encoding="utf-8").splitlines()
    return next(m for m in map(json.loads, lines) if m["id"] == message_id)


@pytest.mark.parametrize("domain,message_id,predicate,value,quote", [
    ("insurance", "ins-001", "policy_renewal_date", "2026-11-14", "Your policy renews on November 14, 2026."),
    ("insurance", "ins-002", "premium_due_date", "2026-10-20", "Premium due date: October 20, 2026"),
    ("insurance", "ins-003", "premium_payment_status", "received", "We received your premium payment of $142.00."),
    ("insurance", "ins-004", "claim_status", "open", "Your claim is open and assigned to an adjuster."),
    ("insurance", "ins-005", "claim_status", "closed", "Claim status: closed."),
    ("travel", "trv-001", "departure_date", "2026-12-18", "Departs December 18, 2026 at 7:05 AM"),
    ("travel", "trv-002", "hotel_checkin_date", "2026-12-18", "Check-in: December 18, 2026 after 3:00 PM"),
    ("travel", "trv-003", "document_expiry_date", "2027-03-02", "Your passport expires on March 2, 2027."),
])
def test_given_a_fixture_message_when_grounded_then_the_value_is_supported(domain, message_id, predicate, value, quote):
    message = fixture(domain, message_id)
    assert quote in message["body"]
    pred = load(domain).predicates[predicate]
    parsed = date.fromisoformat(value) if pred.type == "date" else value
    pred.validate_value(parsed)
    assert supports_value(pred, parsed, quote, observed_at=message["date_iso"], message=message["body"])


def test_given_negated_or_conditional_payment_wording_then_received_is_not_grounded():
    pred = load("insurance").predicates["premium_payment_status"]
    assert "amount not compared" in pred.hint and "insurer name only" in pred.hint
    assert supports_value(pred, "paid", "Your premium is paid in full.")
    assert not supports_value(pred, "received", "We have not received your premium payment.")
    assert not supports_value(pred, "received", "If we received your payment, ignore this notice.")


# ── AC3 alert rules ───────────────────────────────────────────────────────

@pytest.mark.parametrize("predicate,value,expected", [
    ("policy_renewal_date", "2026-10-26", Severity.P2),     # 14 days
    ("policy_renewal_date", "2026-10-27", None),            # 15 days
    ("policy_renewal_date", "2026-10-15", Severity.P1),     # 3 days
    ("policy_renewal_date", "2026-10-09", Severity.P1),     # overdue, outstanding
    ("premium_due_date", "2026-10-17", Severity.P2),        # 5 days
    ("premium_due_date", "2026-10-18", None),               # 6 days
    ("premium_due_date", "2026-10-14", Severity.P1),        # 2 days
    ("premium_due_date", "2026-10-10", Severity.P1),        # overdue, outstanding
    ("claim_status", "open", Severity.P3),
    ("claim_status", "closed", None),
])
def test_given_an_insurance_fact_when_triaged_then_the_rule_fires_at_the_right_day(ledger, predicate, value, expected):
    assert severity_of(ledger, load("insurance"), predicate, value) is expected


@pytest.mark.parametrize("predicate,value,expected", [
    ("departure_date", "2026-10-19", Severity.P3),          # 7 days
    ("departure_date", "2026-10-20", None),                 # 8 days
    ("departure_date", "2026-10-14", Severity.P2),          # 2 days
    ("departure_date", "2026-10-11", None),                 # trip has started: nothing owed
    ("hotel_checkin_date", "2026-10-14", Severity.P3),
    ("hotel_checkin_date", "2026-10-15", None),
    ("hotel_checkin_date", "2026-10-10", None),
    ("document_expiry_date", "2027-04-10", Severity.P2),    # 180 days
    ("document_expiry_date", "2027-04-11", None),           # 181 days
    ("document_expiry_date", "2026-10-01", Severity.P1),    # expired, outstanding
])
def test_given_a_travel_fact_when_triaged_then_the_rule_fires_at_the_right_day(ledger, predicate, value, expected):
    assert severity_of(ledger, load("travel"), predicate, value) is expected


# ── AC4 payment resolves the premium ──────────────────────────────────────

def test_given_a_later_premium_payment_when_triaged_then_the_premium_is_settled(ledger):
    pack = load("insurance")
    assert pack.predicates["premium_payment_status"].resolves == "premium_due_date"
    due = put(ledger, "insurance", "premium_due_date", "2026-10-14", obligation="example insurer")
    paid = put(ledger, "insurance", "premium_payment_status", "received", obligation="example insurer")
    seen = {due: NOW - timedelta(hours=30), paid: NOW - timedelta(hours=2)}
    result = run(ledger, pack, seen)
    assert result.alerts == () and result.horizon == ()


def test_given_a_payment_for_another_insurer_then_the_premium_stays_open(ledger):
    pack = load("insurance")
    due = put(ledger, "insurance", "premium_due_date", "2026-10-14", obligation="example insurer")
    paid = put(ledger, "insurance", "premium_payment_status", "paid", obligation="sample mutual")
    seen = {due: NOW - timedelta(hours=30), paid: NOW - timedelta(hours=2)}
    assert [a.severity for a in run(ledger, pack, seen).alerts] == [Severity.P1]
