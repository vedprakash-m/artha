from datetime import datetime, timezone

import pytest

from catchup_core import corpus, documents
from catchup_core.crypto import EnvelopeCipher, StaticKeyProvider
from catchup_core.contracts import Provenance
from catchup_core.ids import HmacKey
from catchup_core.ledger import FactInput, Ledger, PersonalScopeUnavailable, PermissionDenied

NOW = "2026-10-24T12:00:00Z"


@pytest.fixture
def ledger(tmp_path):
    cipher = EnvelopeCipher(StaticKeyProvider({1: b"e" * 32}))
    with Ledger(tmp_path / "ledger.db", id_key=HmacKey(1, b"i" * 32), cipher=cipher,
                owner_id="owner", clock=lambda: NOW) as value:
        value.migrate()
        yield value


def test_personal_fact_reads_remain_unavailable_before_value_decode(ledger, monkeypatch):
    fact = FactInput("finance", "balance", "synthetic", "owner", NOW, provenance=Provenance.USER_CONFIRMED)
    with monkeypatch.context() as authorized:
        authorized.setattr(ledger, "require_personal_fact_admission", lambda: None)
        fact_id = ledger.put_fact(fact)

    monkeypatch.setattr(ledger, "_decode_value", lambda *_: pytest.fail("unadmitted Fact value was decoded"))
    reads = (
        lambda: ledger.get_fact(fact_id, "owner"),
        lambda: ledger.get_fact_metadata(fact_id, "owner"),
        lambda: ledger.fact_history(fact_id, "owner"),
        lambda: ledger.facts_for("finance", "balance", "owner"),
    )
    for read in reads:
        with pytest.raises(PersonalScopeUnavailable, match="Fact scope unavailable"):
            read()


def test_existing_personal_fact_updates_remain_unavailable(ledger, monkeypatch):
    fact = FactInput("finance", "balance", "synthetic", "owner", NOW, provenance=Provenance.USER_CONFIRMED)
    with monkeypatch.context() as authorized:
        authorized.setattr(ledger, "require_personal_fact_admission", lambda: None)
        fact_id = ledger.put_fact(fact)

    recorded_at = ledger.conn.execute(
        "SELECT recorded_at FROM facts WHERE fact_id=?", (fact_id,)).fetchone()[0]
    with pytest.raises(PersonalScopeUnavailable, match="Fact scope unavailable"):
        ledger.put_fact(fact, fact_id=fact_id)
    assert ledger.conn.execute(
        "SELECT recorded_at FROM facts WHERE fact_id=?", (fact_id,)).fetchone()[0] == recorded_at


def test_personal_document_ingest_remains_unavailable_before_file_access(ledger, tmp_path):
    with pytest.raises(PermissionDenied, match="personal document unavailable"):
        documents.ingest(
            tmp_path / "missing.pdf", "owner", ledger, "owner",
            datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc))


def test_personal_corpus_reads_remain_unavailable(ledger):
    with pytest.raises(PersonalScopeUnavailable, match="personal corpus scope unavailable"):
        corpus.latest_outcome(ledger, "missing")


@pytest.mark.parametrize(
    ("method", "message"),
    [
        ("require_personal_connector_admission", "Connector"),
        ("require_personal_signal_admission", "Signal"),
        ("require_personal_calendar_admission", "calendar"),
    ],
)
def test_other_personal_scope_admissions_remain_unavailable(ledger, method, message):
    with pytest.raises(PersonalScopeUnavailable, match=message):
        getattr(ledger, method)()
