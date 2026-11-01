"""Digital and Pets packs 0.1.0: strict load, synthetic grounding, alert thresholds and triage (W2b)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

from catchup_core import crypto, ids
from catchup_core import domain_pack as dp
from catchup_core import extract as ex
from catchup_core import ledger as lg
from catchup_core.contracts import FactStatus, Provenance, Severity
from catchup_core.triage import triage

PACKS = Path(dp.__file__).parent / "packs"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x07" * 32}))
NOW = datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc)      # 09:00 on 31 October in America/Los_Angeles


def pack(domain):
    return dp.load_pack_file(PACKS / f"{domain}.yaml")


def cases(domain):
    return yaml.safe_load((FIXTURES / domain / "signals.yaml").read_text(encoding="utf-8"))["cases"]


# --- load --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("domain", "connector", "predicates"), [
    ("digital", "digital_email", {"subscription_renewal_date", "domain_expiry_date", "payment_failed"}),
    ("pets", "pets_email", {"vaccination_due_date", "vet_appointment_date", "licence_renewal_date"})])
def test_the_pack_loads_strictly_and_declares_what_it_lacks(domain, connector, predicates):
    p = pack(domain)
    assert p.domain == domain and p.version == "0.1.0" and p.fixtures_dir == f"tests/fixtures/{domain}/"
    assert set(p.predicates) == predicates
    assert [(s.connector, s.required, s.freshness_hours) for s in p.sources] == [(connector, True, 48)]
    assert p.retention is None and "retention" in p.extension_requirements and not p.extension_ready
    assert all(p.predicates[name].hint for name in predicates)


def test_the_predicate_options_match_the_spec():
    digital, pets = pack("digital").predicates, pack("pets").predicates
    assert digital["domain_expiry_date"].high_stakes and digital["domain_expiry_date"].lead_days == 30
    assert digital["subscription_renewal_date"].lead_days == 7 and digital["subscription_renewal_date"].lapse_days == 0
    assert digital["payment_failed"].values == ("failed", "ok")
    assert pets["vaccination_due_date"].lead_days == 14 and pets["licence_renewal_date"].lead_days == 14
    assert pets["vet_appointment_date"].event


def test_the_new_packs_compose_with_every_shipped_pack_without_conflicts():
    shipped = [dp.load_pack_file(p) for p in sorted(PACKS.glob("*.yaml")) if p.stem != "goals"]
    both = dp.composite_pack(shipped)
    assert both.predicate_domains["domain_expiry_date"] == "digital"
    assert both.predicate_domains["vaccination_due_date"] == "pets"
    assert {"digital_email", "pets_email"} <= {s.connector for s in both.sources}


# --- grounding against synthetic fixtures --------------------------------------------------------------------------

def _grounding_cases():
    return [(domain, case) for domain in ("digital", "pets") for case in cases(domain)]


@pytest.mark.parametrize(("domain", "case"), _grounding_cases(), ids=lambda v: v["id"] if isinstance(v, dict) else v)
def test_each_fixture_grounds_its_predicate(domain, case):
    pred = pack(domain).predicates[case["predicate"]]
    value = date.fromisoformat(case["value"]) if pred.type == "date" else case["value"]
    assert case["quote"] in case["message"]
    assert ex.supports_value(pred, value, case["quote"], observed_at=case["observed_at"], message=case["message"])


def test_every_predicate_has_a_fixture():
    for domain in ("digital", "pets"):
        assert {c["predicate"] for c in cases(domain)} == set(pack(domain).predicates)


def test_a_negated_or_hedged_payment_failure_does_not_ground():
    pred = pack("digital").predicates["payment_failed"]
    assert not ex.supports_value(pred, "failed", "Your payment has not failed")
    assert not ex.supports_value(pred, "failed", "If the payment failed we will email you")


def test_extraction_accepts_a_fixture_end_to_end():
    case = cases("pets")[0]
    p = pack("pets")
    start = case["message"].index(case["quote"])
    signal = ex.NormalizedSignal("sig_1", "pets_email", case["message"], observed_at=case["observed_at"])
    out = ('{"complete": true, "candidates": [{"predicate": "%s", "value": "%s", "subject_id": "%s", '
           '"quote": "%s", "span": [%d, %d], "obligation_ref": "rabies", "confidence": 0.8}]}'
           % (case["predicate"], case["value"], case["subject"], case["quote"], start, start + len(case["quote"])))
    parsed = ex.parse_output(out, p, signal, [case["subject"]])
    assert [c.value for c in parsed.candidates] == [date(2026, 11, 8)] and not parsed.rejections


# --- alert thresholds ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("domain", "predicate", "days", "expected"), [
    ("digital", "subscription_renewal_date", 8, None), ("digital", "subscription_renewal_date", 7, "P3"),
    ("digital", "subscription_renewal_date", 3, "P3"), ("digital", "subscription_renewal_date", 2, "P2"),
    ("digital", "subscription_renewal_date", 0, "P2"), ("digital", "subscription_renewal_date", -1, None),
    ("digital", "domain_expiry_date", 31, None), ("digital", "domain_expiry_date", 30, "P2"),
    ("digital", "domain_expiry_date", 8, "P2"), ("digital", "domain_expiry_date", 7, "P1"),
    ("digital", "domain_expiry_date", 0, "P1"), ("digital", "domain_expiry_date", -1, None),
    ("pets", "vaccination_due_date", 15, None), ("pets", "vaccination_due_date", 14, "P2"),
    ("pets", "vaccination_due_date", 0, "P2"), ("pets", "vaccination_due_date", -1, None),
    ("pets", "vet_appointment_date", 8, None), ("pets", "vet_appointment_date", 7, "P3"),
    ("pets", "vet_appointment_date", 0, "P3"),
    ("pets", "licence_renewal_date", 15, None), ("pets", "licence_renewal_date", 14, "P2"),
    ("pets", "licence_renewal_date", 0, "P2")])
def test_date_rules_fire_on_the_boundary_day_and_not_one_day_beyond(domain, predicate, days, expected):
    got = pack(domain).evaluate(predicate, {"days_until": days, "obligation_state": "outstanding"}).severity
    assert (got.value if got else None) == expected


@pytest.mark.parametrize(("value", "expected"), [("failed", "P2"), ("ok", None)])
def test_a_failed_payment_is_p2_and_an_ok_payment_is_quiet(value, expected):
    got = pack("digital").evaluate("payment_failed", {"value": value, "obligation_state": "outstanding"}).severity
    assert (got.value if got else None) == expected


# --- triage through the unchanged core -----------------------------------------------------------------------------

@pytest.fixture
def ledger(tmp_path):
    led = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, owner_id="ved").open()
    led.migrate()
    yield led
    led.close()


def put(led, domain, predicate, value, subject):
    sid, _ = led.upsert_signal(lg.SignalInput(f"{domain}_email", "acct", f"u-{predicate}-{value}-{subject}", "v1",
                                              "t", f"h-{predicate}-{value}-{subject}", "b"))
    led.put_fact(lg.FactInput(
        domain=domain, predicate=predicate, value=value, primary_subject_id=subject, observed_at="t",
        status=FactStatus.CURRENT, provenance=Provenance.VERIFIED_SOURCE, evidence=((sid, 0, 4),)))


def run(led, p, connector):
    def connector_of(sid):
        return led.conn.execute("SELECT connector FROM signals WHERE signal_id=?", (sid,)).fetchone()[0]
    return triage(led, p, NOW, viewer="ved", connector_of=connector_of, freshness={connector: NOW})


@pytest.mark.parametrize("domain", ["digital", "pets"])
def test_fixture_facts_triage_to_the_expected_alerts_and_past_dates_are_quiet(ledger, domain):
    for case in cases(domain):
        put(ledger, domain, case["predicate"], case["value"], case["subject"])
    put(ledger, domain, "subscription_renewal_date" if domain == "digital" else "vet_appointment_date",
        "2026-10-30", "lapsed")
    result = run(ledger, pack(domain), f"{domain}_email")
    got = {(a.predicate, a.subject_id): (a.severity, a.days_until) for a in result.alerts}
    expected = {
        "digital": {("subscription_renewal_date", "example_streaming"): (Severity.P3, 4),
                    ("domain_expiry_date", "example_family_test"): (Severity.P2, 15),
                    ("payment_failed", "example_cloud_notes"): (Severity.P2, None),
                    ("payment_failed", "example_photo_editor"): (Severity.P2, None)},
        "pets": {("vaccination_due_date", "pet_biscuit"): (Severity.P2, 8),
                 ("vet_appointment_date", "pet_biscuit"): (Severity.P3, 3),
                 ("licence_renewal_date", "pet_biscuit"): (Severity.P2, 12)}}[domain]
    assert got == expected
    assert all(a.domain == domain for a in result.alerts)
    assert not any(a.subject_id == "lapsed" for a in (*result.alerts, *result.horizon))
