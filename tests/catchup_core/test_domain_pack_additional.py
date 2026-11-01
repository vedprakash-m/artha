"""An additional Domain (vehicle) runs through the unchanged core; the core names no Domain (RA-14)."""
from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from catchup_core import crypto, ids
from catchup_core import domain_pack as dp
from catchup_core import ledger as lg
from catchup_core.contracts import FactStatus, Provenance, Severity
from catchup_core.triage import triage

CORE = Path(dp.__file__).parent
PACKS = CORE / "packs"
KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x07" * 32}))
NOW = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)

# Modules that already name a Domain today.  This list may only shrink.  Each entry is tracked as an open decision
# in specs/features/re-artha-product-contracts-amendment.md.
KNOWN_DOMAIN_NAMING = {"extract.py", "corpus_cli.py", "runtime.py",   # a Domain default or allow-list
                       "cli.py", "commands.py", "mcp.py"}             # "health" is a command name, not a Domain


@pytest.fixture
def ledger(tmp_path):
    led = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, owner_id="ved").open()
    led.migrate()
    yield led
    led.close()


def put(led, predicate, value, subject="vehicle1"):
    sid, _ = led.upsert_signal(lg.SignalInput("gmail", "acct", f"u-{predicate}-{value}", "v1", "t",
                                              f"h-{predicate}-{value}", "b"))
    return led.put_fact(lg.FactInput(
        domain="vehicle", predicate=predicate, value=value, primary_subject_id=subject, observed_at="t",
        status=FactStatus.CURRENT, provenance=Provenance.VERIFIED_SOURCE, evidence=((sid, 0, 4),)))


def run(led, pack):
    def connector_of(sid):
        return led.conn.execute("SELECT connector FROM signals WHERE signal_id=?", (sid,)).fetchone()[0]
    return triage(led, pack, NOW, viewer="ved", connector_of=connector_of,
                  freshness={"gmail": NOW})


def test_given_the_vehicle_pack_when_loaded_then_it_is_strict_and_declares_what_it_lacks():
    pack = dp.load_pack_file(PACKS / "vehicle.yaml")
    assert pack.domain == "vehicle" and pack.version == "0.3.0"
    # No retention period is invented: it stays unset and is reported as missing.
    assert pack.retention is None and "retention" in pack.extension_requirements
    assert not pack.extension_ready


def test_given_a_vehicle_registration_due_soon_when_triaged_then_the_core_alerts_without_vehicle_code(ledger):
    pack = dp.load_pack_file(PACKS / "vehicle.yaml")
    put(ledger, "registration_renewal_date", "2026-10-14")
    result = run(ledger, pack)
    alert = result.alerts[0]
    assert alert.domain == "vehicle" and alert.predicate == "registration_renewal_date"
    assert alert.severity is Severity.P1 and alert.days_until == 2 and alert.state == "actionable"


def test_given_an_open_recall_when_triaged_then_it_is_p2_and_a_remedied_recall_is_quiet(ledger):
    pack = dp.load_pack_file(PACKS / "vehicle.yaml")
    put(ledger, "recall_status", "open", subject="vehicle1")
    put(ledger, "recall_status", "remedied", subject="vehicle2")
    severities = {a.subject_id: a.severity for a in run(ledger, pack).alerts if a.severity}
    assert severities == {"vehicle1": Severity.P2}


def test_recall_issued_wording_grounds_an_open_recall_and_negations_do_not():
    from catchup_core.extract import supports_value
    pred = dp.load_pack_file(PACKS / "vehicle.yaml").predicates["recall_status"]
    assert "issued" not in pred.values and pred.aliases == (("open", ("issued",)),)
    assert supports_value(pred, "open", "Recall Issued: new recall record for your 2021 Rivian")
    assert supports_value(pred, "open", "Open Recall")
    assert not supports_value(pred, "open", "No recall has been issued for your vehicle")
    assert not supports_value(pred, "open", "A recall may be issued later")


def test_given_the_vehicle_pack_composed_with_another_then_both_domains_stay_separate():
    vehicle = dp.load_pack_file(PACKS / "vehicle.yaml")
    home = dp.load_pack_file(PACKS / "home.yaml")
    both = dp.composite_pack([vehicle, home])
    assert both.predicate_domains["recall_status"] == "vehicle"
    assert both.predicate_domains["mortgage_due_date"] == "home"


def _domain_names() -> set[str]:
    # "travel" is also a calendar-event kind (triage.CalendarEvent.kind), not a Domain reference.
    return {p.stem for p in PACKS.glob("*.yaml")} - {"goals", "travel"}


def _domain_literals(path: Path, names: set[str]) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in names:
            found.add(node.value)
        elif isinstance(node, ast.Name) and node.id in names:
            found.add(node.id)
    return found


def test_given_the_core_modules_when_scanned_then_no_new_module_names_a_domain():
    names = _domain_names()
    assert {"vehicle", "kids_school"} <= names
    offenders = {p.name: sorted(_domain_literals(p, names)) for p in CORE.glob("*.py")
                 if p.name not in KNOWN_DOMAIN_NAMING and _domain_literals(p, names)}
    assert offenders == {}


def test_given_the_owned_core_modules_then_none_names_a_domain_even_on_the_known_list():
    names = _domain_names()
    for module in ("domain_pack.py", "goals.py", "views.py", "compose.py", "curated.py", "triage.py"):
        assert _domain_literals(CORE / module, names) == set(), module


def test_given_the_known_list_when_a_module_stops_naming_a_domain_then_the_list_must_shrink():
    names = _domain_names()
    stale = [m for m in KNOWN_DOMAIN_NAMING if not _domain_literals(CORE / m, names)]
    assert stale == []
