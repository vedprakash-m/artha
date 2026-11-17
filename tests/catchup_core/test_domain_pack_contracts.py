"""Pack contract evaluation: effective interval, jurisdiction, quantity, permission and version moves (RA-14)."""
from __future__ import annotations

from datetime import date

import pytest

from catchup_core import domain_pack as dp


def pred(**extra):
    base = {"name": "p", "type": "date", "authority": ["a"]}
    return dp.load_pack({"domain": "d", "version": "1.0.0", "confidentiality": "ordinary", "impact": "advisory",
                         "sources": [], "predicates": [{**base, **extra}]}).predicates["p"]


def test_given_no_declarations_when_checked_then_the_predicate_applies_everywhere():
    assert pred().applies(date(2026, 1, 1), None) is True


@pytest.mark.parametrize(("day", "expected"), [(date(2026, 1, 1), True), (date(2026, 12, 31), True),
                                               (date(2027, 1, 1), False), (date(2025, 12, 31), False)])
def test_given_an_interval_when_checked_then_start_is_inclusive_and_end_is_exclusive(day, expected):
    p = pred(effective_interval={"start": "2026-01-01", "end": "2027-01-01"})
    assert p.applies(day, None) is expected


def test_given_a_jurisdiction_when_none_is_supplied_then_the_answer_is_unknown_not_true():
    assert pred(jurisdiction=["US-WA"]).applies(date(2026, 1, 1), None) is None


@pytest.mark.parametrize(("declared", "given", "expected"), [
    (["US-WA"], "US-WA", True), (["US-WA"], "US-OR", False), (["US"], "US-WA", True),
    (["US-WA"], "US", False), (["US-WA", "CA-BC"], "CA-BC", True)])
def test_given_jurisdictions_when_compared_then_a_declared_parent_covers_its_subdivisions(declared, given, expected):
    assert pred(jurisdiction=declared).applies(date(2026, 1, 1), given) is expected


def test_given_both_declarations_when_the_interval_fails_then_false_wins_over_unknown():
    p = pred(jurisdiction=["US-WA"], effective_interval={"start": "2026-01-01", "end": "2027-01-01"})
    assert p.applies(date(2028, 1, 1), None) is False
    assert p.applies(date(2026, 6, 1), None) is None


def test_given_a_quantity_when_unit_or_currency_differs_then_it_is_not_accepted():
    p = pred(quantity={"unit": "dollars", "currency": "USD"})
    assert p.accepts_quantity("dollars", "USD") is True
    assert p.accepts_quantity("dollars", "EUR") is False
    assert p.accepts_quantity("dollars", None) is False
    assert p.accepts_quantity("miles", "USD") is False
    assert pred().accepts_quantity("dollars", None) is False        # nothing declared: nothing is accepted


def with_permissions(rules):
    return dp.load_pack({"domain": "d", "version": "1.0.0", "confidentiality": "ordinary", "impact": "advisory",
                         "sources": [], "permissions": rules,
                         "predicates": [{"name": "p", "type": "date", "authority": ["a"]},
                                        {"name": "q", "type": "date", "authority": ["a"]}]})


def test_given_allow_and_deny_rules_when_resolved_then_deny_wins_and_unmatched_is_unknown():
    pack = with_permissions([{"scope": "viewer", "predicates": ["p", "q"], "effect": "allow"},
                             {"scope": "viewer", "predicates": ["q"], "effect": "deny"}])
    assert pack.permission("viewer", "p") == "allow"
    assert pack.permission("viewer", "q") == "deny"
    assert pack.permission("other", "p") is None
    with pytest.raises(dp.PackError):
        pack.permission("viewer", "missing")


def test_given_no_permissions_declared_when_resolved_then_nothing_is_allowed():
    pack = dp.load_pack_file(dp.__file__.replace("domain_pack.py", "packs/vehicle.yaml"))
    assert pack.permissions is None and pack.permission("viewer", "recall_status") is None


def moving(**extra):
    return dp.load_pack({"domain": "d", "version": "2.0.0", "confidentiality": "ordinary", "impact": "advisory",
                         "sources": [], "predicates": [{"name": "p", "type": "date", "authority": ["a"]}], **extra})


def test_given_a_declared_migration_and_rollback_when_asked_then_only_those_moves_are_answered():
    pack = moving(migration={"from_version": "1.0.0", "to_version": "2.0.0", "compatible": True},
                  rollback={"from_version": "2.0.0", "to_version": "1.0.0", "compatible": False})
    assert pack.can_move("1.0.0", "2.0.0") is True
    assert pack.can_move("2.0.0", "1.0.0") is False        # declared as incompatible
    assert pack.can_move("0.9.0", "2.0.0") is None         # never declared: unknown
    assert moving().can_move("1.0.0", "2.0.0") is None
    assert pack.can_move("2.0.0", "2.0.0") is True         # staying on the same version needs nothing


# ── Insurance & Travel packs (RA-14) ─────────────────────────────────────────

def _ins_load(domain: str):
    from pathlib import Path
    return dp.load_pack_file(Path(dp.__file__).parent / "packs" / f"{domain}.yaml")


@pytest.fixture
def _ins_ledger(tmp_path):
    from catchup_core import crypto, ids
    from catchup_core import ledger as lg

    key = ids.HmacKey(1, b"i" * 32)
    cipher = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x07" * 32}))
    led = lg.Ledger(tmp_path / "l.db", id_key=key, cipher=cipher, owner_id="ved").open()
    led.migrate()
    yield led
    led.close()


def _ins_put(led, domain, predicate, value, subject="ved", obligation=None):
    from catchup_core import ledger as lg

    tag = f"{subject}-{predicate}-{value}-{obligation}"
    sid, _ = led.upsert_signal(lg.SignalInput(f"{domain}_email", "acct", f"u-{tag}", "v1", "t", f"h-{tag}", "b"))
    extra = {"obligation_key": obligation} if obligation else {}
    led.put_fact(lg.FactInput(domain=domain, predicate=predicate, value=value, primary_subject_id=subject,
                              observed_at="t", evidence=((sid, 0, 4),), **extra))
    return sid


def _ins_run(led, pack, seen=None):
    from datetime import datetime, timezone
    from catchup_core.triage import triage

    now = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)

    def connector_of(sid):
        return led.conn.execute("SELECT connector FROM signals WHERE signal_id=?", (sid,)).fetchone()[0]
    return triage(led, pack, now, viewer="ved", connector_of=connector_of, freshness={f"{pack.domain}_email": now, **(seen or {})})


@pytest.mark.parametrize("domain,predicates", [
    ("insurance", {"policy_renewal_date", "premium_due_date", "premium_payment_status", "claim_status"}),
    ("travel", {"departure_date", "hotel_checkin_date", "document_expiry_date"}),
])
def test_given_the_new_pack_when_loaded_then_it_is_strict_and_declares_what_it_lacks(domain, predicates):
    pack = _ins_load(domain)
    assert pack.domain == domain and pack.version == "0.1.0"
    assert set(pack.predicates) == predicates
    assert [(s.connector, s.required, s.freshness_hours) for s in pack.sources] == [(f"{domain}_email", True, 48)]
    assert pack.retention is None and "retention" in pack.extension_requirements
    assert pack.fixtures_dir == f"tests/fixtures/{domain}/"


def test_given_every_shipped_pack_when_composed_then_predicate_ownership_is_unambiguous():
    from pathlib import Path

    packs = [dp.load_pack_file(p) for p in sorted((Path(dp.__file__).parent / "packs").glob("*.yaml"))]
    both = dp.composite_pack(packs)
    assert both.predicate_domains["premium_payment_status"] == "insurance"
    assert both.predicate_domains["document_expiry_date"] == "travel"
    assert both.predicate_domains["payment_status"] == "finance"


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
    import json
    from pathlib import Path
    from catchup_core.extract import supports_value

    lines = (Path(__file__).resolve().parent.parent / "fixtures" / domain / "emails.jsonl").read_text(encoding="utf-8").splitlines()
    message = next(m for m in map(json.loads, lines) if m["id"] == message_id)
    assert quote in message["body"]
    pred_obj = _ins_load(domain).predicates[predicate]
    parsed = date.fromisoformat(value) if pred_obj.type == "date" else value
    pred_obj.validate_value(parsed)
    assert supports_value(pred_obj, parsed, quote, observed_at=message["date_iso"], message=message["body"])


def test_given_negated_or_conditional_payment_wording_then_received_is_not_grounded():
    from catchup_core.extract import supports_value

    pred_obj = _ins_load("insurance").predicates["premium_payment_status"]
    assert "amount not compared" in pred_obj.hint and "insurer name only" in pred_obj.hint
    assert supports_value(pred_obj, "paid", "Your premium is paid in full.")
    assert not supports_value(pred_obj, "received", "We have not received your premium payment.")
    assert not supports_value(pred_obj, "received", "If we received your payment, ignore this notice.")


@pytest.mark.parametrize("domain,predicate,value,expected_name", [
    ("insurance", "policy_renewal_date", "2026-10-26", "P2"),
    ("insurance", "policy_renewal_date", "2026-10-27", None),
    ("insurance", "policy_renewal_date", "2026-10-15", "P1"),
    ("insurance", "policy_renewal_date", "2026-10-09", "P1"),
    ("insurance", "premium_due_date", "2026-10-17", "P2"),
    ("insurance", "premium_due_date", "2026-10-18", None),
    ("insurance", "premium_due_date", "2026-10-14", "P1"),
    ("insurance", "premium_due_date", "2026-10-10", "P1"),
    ("insurance", "claim_status", "open", "P3"),
    ("insurance", "claim_status", "closed", None),
    ("travel", "departure_date", "2026-10-19", "P3"),
    ("travel", "departure_date", "2026-10-20", None),
    ("travel", "departure_date", "2026-10-14", "P2"),
    ("travel", "departure_date", "2026-10-11", None),
    ("travel", "hotel_checkin_date", "2026-10-14", "P3"),
    ("travel", "hotel_checkin_date", "2026-10-15", None),
    ("travel", "hotel_checkin_date", "2026-10-10", None),
    ("travel", "document_expiry_date", "2027-04-10", "P2"),
    ("travel", "document_expiry_date", "2027-04-11", None),
    ("travel", "document_expiry_date", "2026-10-01", "P1"),
])
def test_given_insurance_or_travel_fact_when_triaged_then_rule_fires_at_right_day(
    _ins_ledger, domain, predicate, value, expected_name, admitted_personal_sources
):
    from catchup_core.contracts import Severity

    pack = _ins_load(domain)
    _ins_put(_ins_ledger, domain, predicate, value)
    alerts = [a for a in _ins_run(_ins_ledger, pack).alerts if a.predicate == predicate]
    actual = alerts[0].severity if alerts else None
    assert actual is (None if expected_name is None else getattr(Severity, expected_name))


def test_given_insurance_premium_payment_resolution_rules(_ins_ledger, admitted_personal_sources):
    from datetime import datetime, timedelta, timezone
    from catchup_core.contracts import Severity

    now = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)
    pack = _ins_load("insurance")
    assert pack.predicates["premium_payment_status"].resolves == "premium_due_date"
    due = _ins_put(_ins_ledger, "insurance", "premium_due_date", "2026-10-14", obligation="example insurer")
    paid = _ins_put(_ins_ledger, "insurance", "premium_payment_status", "received", obligation="example insurer")
    res = _ins_run(_ins_ledger, pack, {due: now - timedelta(hours=30), paid: now - timedelta(hours=2)})
    assert res.alerts == () and res.horizon == ()

    due2 = _ins_put(_ins_ledger, "insurance", "premium_due_date", "2026-10-14", subject="other", obligation="example insurer")
    paid2 = _ins_put(_ins_ledger, "insurance", "premium_payment_status", "paid", subject="other", obligation="sample mutual")
    assert [a.severity for a in _ins_run(_ins_ledger, pack, {due2: now - timedelta(hours=30), paid2: now - timedelta(hours=2)}).alerts] == [Severity.P1]
