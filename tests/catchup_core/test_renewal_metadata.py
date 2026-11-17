"""Purpose-limited authority inspection never opens a writable runtime or personal evidence."""
import hashlib
from datetime import UTC, datetime

import pytest

from catchup_core import renewal_metadata
from catchup_core.ledger import KeyringSealedStore, Ledger
from catchup_core.renewal_store import RenewalError
from core_helpers import (
    CORE_KEY as KEY,
    Keyring,
    REGRESSION_CIPHER as CIPHER,
    renewal_private_directory as private_directory,
)

NOW = datetime(2026, 1, 2, tzinfo=UTC)


@pytest.fixture
def metadata(tmp_path):
    local = tmp_path / 'local'
    private_directory(local)
    backend = Keyring()
    privacy = tmp_path / 'privacy'
    sealed = KeyringSealedStore(backend, 'authoritative', cipher=CIPHER, history_root=privacy)
    ledger = Ledger(local / 'ledger.db', id_key=KEY, cipher=CIPHER, sealed=sealed)
    ledger.open()
    ledger.migrate()
    ledger.ensure_authority_host('synthetic')
    ledger.seal_privacy()
    ledger.close()
    reader = renewal_metadata.AuthorityMetadata(local, backend, 'synthetic', cipher=CIPHER,
                                                history_root=privacy, now=lambda: NOW)
    return reader, ledger, backend


def test_AR24_inspection_is_readonly_and_never_constructs_ledger(metadata, monkeypatch):
    reader, ledger, backend = metadata
    before = {p.name: p.read_bytes() for p in reader.local.iterdir() if p.is_file()}
    native = dict(backend.store)
    monkeypatch.setattr(Ledger, '__init__', lambda *_a, **_kw: pytest.fail('constructed writable/runtime Ledger'))
    token = reader.snapshot()
    assert token == reader.snapshot() and len(token) == 64
    assert native == backend.store
    assert before == {p.name: p.read_bytes() for p in reader.local.iterdir() if p.is_file()}


def test_AR05_consent_change_and_expiry_change_attempt_identity(metadata):
    reader, ledger, _ = metadata
    first = reader.snapshot()
    ledger.open()
    ledger.grant_consent('subject', 'owner', 'synthetic', ['view'], 'owner', expires_at='2026-01-03T00:00:00+00:00')
    ledger.seal_privacy()
    second = reader.snapshot(connection=ledger.conn)
    assert second != first
    reader.now = lambda: datetime(2026, 1, 4, tzinfo=UTC)
    assert reader.snapshot(connection=ledger.conn) != second
    ledger.close()


def test_AR11_missing_anchor_and_stale_privacy_history_refuse(metadata):
    reader, ledger, backend = metadata
    backend.store.clear()
    with pytest.raises(RenewalError):
        reader.snapshot()


def test_AR24_noncheckpointed_wal_is_held_without_repair(metadata):
    reader, ledger, _ = metadata
    ledger.open()
    ledger.conn.execute("INSERT INTO authority_generation VALUES(2,'synthetic','now',NULL)")
    with pytest.raises(RenewalError):
        reader.snapshot()
    ledger.close()
