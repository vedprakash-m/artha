"""GAP-STALE: old evidence lowers confidence, it does not remove the severity a rule gives (SS-01..SS-03)."""
from __future__ import annotations

from datetime import timedelta

import pytest
import yaml

from catchup_core import domain_pack as dp
from catchup_core import ledger as lg
from catchup_core.contracts import Severity
from catchup_core.triage import triage

from test_triage import KEY, CIPHER, NOW, conn_of  # noqa: F401


@pytest.fixture
def env(tmp_path):
    ledger = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, owner_id="ved").open()
    ledger.migrate()
    yield ledger
    ledger.close()


def vehicle():
    return dp.load_pack(yaml.safe_load(open(dp.__file__.replace("domain_pack.py", "packs/vehicle.yaml"))))


def recall(ledger, value, hours_old):
    sid, _ = ledger.upsert_signal(lg.SignalInput("vehicle_email", "acct", f"u-{value}", "v1", "t", f"h-{value}", "b"))
    ledger.put_fact(lg.FactInput(domain="vehicle", predicate="recall_status", value=value,
                                 primary_subject_id="ved", observed_at="t", evidence=((sid, 0, 4),)))
    return {sid: NOW - timedelta(hours=hours_old)}


def alerts(ledger, seen):
    return triage(ledger, vehicle(), NOW, viewer="ved", connector_of=conn_of(ledger), freshness=seen).alerts


def test_an_old_open_recall_keeps_its_severity_and_says_it_is_old(env):
    [alert] = alerts(env, recall(env, "open", hours_old=200))
    assert alert.severity is Severity.P2 and alert.state == "unknown_stale"                         # SS-01
    assert "200h old" in alert.uncertainty


def test_a_fresh_open_recall_is_unchanged(env):
    [alert] = alerts(env, recall(env, "open", hours_old=1))
    assert alert.severity is Severity.P2 and alert.state == "actionable"                            # SS-02


def test_an_old_remedied_recall_never_gains_a_severity(env):
    assert all(a.severity is None for a in alerts(env, recall(env, "remedied", hours_old=200)))      # SS-03
