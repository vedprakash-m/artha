import pytest

from catchup_core.ledger import Ledger


@pytest.fixture
def admitted_personal_scope(monkeypatch):
    """Exercise downstream Ledger behavior with synthetic test-only admission."""
    for method in (
        "require_personal_connector_admission",
        "require_personal_signal_admission",
        "require_personal_fact_admission",
        "require_personal_calendar_admission",
    ):
        monkeypatch.setattr(Ledger, method, lambda self: None)

    def bind_synthetic_fact(self, db, fact_id, version, content_hmac, _provenance):
        self._record_personal_binding(
            db, "fact", fact_id, version, "ledger", self._owner, content_hmac)
        self._record_personal_binding(
            db, "fact_version", fact_id, version, "ledger", self._owner, content_hmac)

    monkeypatch.setattr(Ledger, "_bind_fact_after_write", bind_synthetic_fact)


@pytest.fixture
def admitted_documentary_scope(monkeypatch, admitted_personal_scope):
    """Exercise reviewed document behavior with synthetic test-only admission."""
    from catchup_core import fact_access, field_confirmation

    monkeypatch.setattr(fact_access, "require_documentary_scope", lambda: None)
    monkeypatch.setattr(field_confirmation, "require_documentary_scope", lambda: None)


@pytest.fixture
def admitted_personal_snapshot(monkeypatch, admitted_personal_scope):
    """Exercise snapshot export with synthetic test-only admission."""
    from catchup_core import commands

    monkeypatch.setattr(commands, "_require_personal_snapshot_admission", lambda: None)


@pytest.fixture
def admitted_curated_state(monkeypatch):
    """Review synthetic State Files before exercising their downstream projections."""
    from catchup_core import curated, ids

    def identity(directory, name, ledger):
        path = directory / f"{name}.md"
        info = path.lstat()
        version = f"{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}"
        digest = ids.content_hmac(ledger._id_key, version)
        if not ledger._has_personal_binding("curated", name, version, "state", ledger._owner, digest):
            with ledger.transaction() as db:
                ledger._record_personal_binding(
                    db, "curated", name, version, "state", ledger._owner, digest, provenance="source_review")
        return path, version, digest

    monkeypatch.setattr(curated, "_personal_state_identity", identity)


@pytest.fixture
def admitted_personal_sources(monkeypatch, admitted_personal_scope):
    """Create source-reviewed Signals for tests that exercise ingestion downstream."""
    upsert_signal = Ledger.upsert_signal

    def upsert_reviewed_signal(self, signal):
        signal_id, created = upsert_signal(self, signal)
        row = self.signal_row(signal_id)
        if not self._has_personal_binding(
            "signal", signal_id, row["upstream_version"], row["connector"],
            row["account_scope"], row["content_hmac"],
        ):
            with self.transaction() as db:
                self._record_personal_binding(
                    db, "signal", signal_id, row["upstream_version"], row["connector"],
                    row["account_scope"], row["content_hmac"], provenance="source_review")
        return signal_id, created

    monkeypatch.setattr(Ledger, "upsert_signal", upsert_reviewed_signal)


@pytest.fixture
def admitted_personal_corpus_access(monkeypatch, admitted_personal_scope):
    """Exercise corpus access checks with synthetic test-only admission."""
    from catchup_core import corpus

    monkeypatch.setattr(corpus, "_require_personal_corpus_admission", lambda: None)


@pytest.fixture
def admitted_personal_corpus(admitted_personal_sources, admitted_personal_corpus_access):
    """Exercise corpus ingestion with synthetic source review and scope admission."""
