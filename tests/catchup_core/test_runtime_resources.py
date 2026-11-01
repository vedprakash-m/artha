"""Runtime construction transfers handle ownership only after every dependency is ready."""
from __future__ import annotations

from pathlib import Path

import pytest

from catchup_core import runtime
from catchup_core.contracts import CatchupRequest, ExecutionMode
from catchup_core.ledger import Ledger, LedgerError
from test_runtime import FakeKeyring, write_cfg


@pytest.fixture
def builder(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    (repo / 'state').mkdir(parents=True)
    write_cfg(repo)
    opened = []
    original = runtime._open_ledger
    def tracked(*args, **kwargs):
        result = original(*args, **kwargs)
        opened.append(result[0])
        return result
    monkeypatch.setattr(runtime, '_open_ledger', tracked)
    def build(**kwargs):
        loaders = {'handler_loader': lambda _: None, 'auth_loader': lambda _: {}}
        loaders.update(kwargs)
        return runtime.build_deps(CatchupRequest('cu_resources', 'ved', 'cli', mode=ExecutionMode.SHADOW),
            repo_root=repo, environ={'ARTHA_LOCAL_DIR': str(tmp_path / 'private')}, keyring_backend=FakeKeyring(),
            **loaders)
    yield build, opened
    for ledger in opened:
        ledger.close()


@pytest.mark.parametrize('target', ['build_fetchers', '_bundle_fetchers', '_implementation_fingerprint',
                                    'LegacyStateAdapter', 'command_extractor', '_default_loaders'])
@pytest.mark.parametrize('error', [OSError, KeyboardInterrupt])
def test_actual_builder_closes_open_ledger_after_a_wiring_failure(builder, monkeypatch, target, error):
    build, opened = builder
    original_config = runtime.load_runtime_config
    def configuration(*args):
        config = original_config(*args)
        config.extractor_cmd = 'python scripts/catchup_core/extractor_cmd.py'
        return config
    monkeypatch.setattr(runtime, 'load_runtime_config', configuration)
    def fail(*args, **kwargs):
        raise error('synthetic setup failure')
    monkeypatch.setattr(runtime, target, fail)
    with pytest.raises(error, match='synthetic setup failure'):
        build(**({'handler_loader': None} if target == '_default_loaders' else {}))
    assert len(opened) == (0 if target == '_implementation_fingerprint' else 1)
    for ledger in opened:
        with pytest.raises(LedgerError, match='not open'):
            _ = ledger.conn


def test_successful_runtime_transfers_an_open_handle_to_its_caller(builder):
    build, opened = builder
    deps = build()
    assert deps.ledger is opened[0]
    assert deps.ledger.conn.execute('SELECT 1').fetchone()[0] == 1


def test_runtime_construction_and_execution_share_the_checkpoint_fence(builder, tmp_path):
    from catchup_core.session_lock import SessionBusy, session_lock
    build, opened = builder
    lock = tmp_path / 'private/installation.lock'
    with session_lock(lock):
        with pytest.raises(SessionBusy):
            build()
    assert not opened
    deps = build()
    with session_lock(lock), pytest.raises(SessionBusy):
        with deps.maintenance():
            pytest.fail('checkpoint fence was ignored')


def test_interrupted_inner_ledger_setup_closes_its_handle(tmp_path, monkeypatch):
    from catchup_core.crypto import EnvelopeCipher, KeyringKeyProvider
    from test_coordinator import KEY
    keyring = FakeKeyring()
    cipher = EnvelopeCipher(KeyringKeyProvider('synthetic-resource-key', backend=keyring))
    captured = []
    original = Ledger.open
    def track(self, **kwargs):
        result = original(self, **kwargs)
        captured.append(self)
        return result
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt('synthetic migration interruption')
    monkeypatch.setattr(Ledger, 'open', track)
    monkeypatch.setattr(Ledger, 'migrate', interrupt)
    config = runtime.RuntimeConfig({}, ['owner'], 'owner')
    request = CatchupRequest('cu_interrupted', 'owner', 'cli', mode=ExecutionMode.SHADOW)
    with pytest.raises(KeyboardInterrupt, match='synthetic migration interruption'):
        runtime._open_ledger(tmp_path, config, request, cipher, KEY, keyring)
    assert len(captured) == 1
    with pytest.raises(LedgerError, match='not open'):
        _ = captured[0].conn


@pytest.mark.parametrize('days', [366, 3660])
def test_retention_rejected_before_runtime_when_snapshot_pruner_would_refuse_it(tmp_path, days):
    repo = tmp_path / 'repo'
    repo.mkdir()
    write_cfg(repo, snapshot_retention_days=days)
    with pytest.raises(runtime.RuntimeConfigError, match='snapshot_retention_days'):
        runtime.load_runtime_config(repo / 'config/catchup_core.yaml')


def test_retention_acceptance_matches_pruner_bounds_and_keeps_bootstrap_independent(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    for days in (1, 365):
        write_cfg(repo, snapshot_retention_days=days, bootstrap_days=3660)
        config = runtime.load_runtime_config(repo / 'config/catchup_core.yaml')
        assert config.policy['snapshot_retention_days'] == days
        assert config.policy['bootstrap_days'] == 3660


def test_real_runtime_wires_a_private_spend_seal_that_the_recovery_registry_names(tmp_path, monkeypatch):
    """Given resource limits, when the first request is admitted, then the seal exists outside the ledger folder."""
    from datetime import UTC, datetime

    from catchup_core import recovery_registry
    from catchup_core.ledger import SignalInput
    from catchup_core.resources import FileSpendSeal
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')     # the canonical registry lives under home
    (tmp_path / 'home').mkdir()
    repo = tmp_path / 'repo'
    (repo / 'state').mkdir(parents=True)
    limits = dict(max_requests_per_month=20, max_pending_signals=5)
    write_cfg(repo, resource_limits=limits)
    private = tmp_path / 'private'
    deps = runtime.build_deps(CatchupRequest('sealed-runtime', 'ved', 'cli', mode=ExecutionMode.SHADOW),
        repo_root=repo, environ={'ARTHA_LOCAL_DIR': str(private)}, keyring_backend=FakeKeyring(),
        handler_loader=lambda _: None, auth_loader=lambda _: {})
    try:
        store = deps.admissions
        assert isinstance(store.seal, FileSpendSeal)
        ident = deps.ledger.upsert_signal(SignalInput('mail', 'one', 'a', '1', '2026-10-01T00:00:00Z', 'a', 'blob'))[0]
        deps.ledger.conn.execute("UPDATE signals SET processing_state='normalized' WHERE signal_id=?", (ident,))
        assert store.admit('c1', ident, 'k1', 10, datetime(2026, 10, 24, tzinfo=UTC)) is True
        seal = private / 'spend-seal' / 'shadow.json'
        assert seal.is_file() and not seal.is_relative_to(private / 'catchup_core')
        assert recovery_registry.spend_seal_enrolled('personal')                        # same registry capture reads
        assert not recovery_registry.spend_seal_enrolled('personal', root=private)
        assert store.seal.read().requests == 1
    finally:
        deps.ledger.close()


def test_runtime_spend_namespaces_enroll_independently_and_refuse_lost_history(tmp_path, monkeypatch):
    from catchup_core.resources import SpendRecord, SpendSealError

    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: home)
    env = {'ARTHA_LOCAL_DIR': str(tmp_path / 'local')}
    shadow = runtime._spend_seal(env, 'shadow')
    authoritative = runtime._spend_seal(env, 'authoritative')
    shadow.advance(SpendRecord(1, '2026-10', 1, 10, frozenset()))
    assert authoritative.read() is None
    authoritative.advance(SpendRecord(1, '2026-10', 1, 20, frozenset()))
    assert shadow.read().tokens == 10 and authoritative.read().tokens == 20
    (tmp_path / 'local/spend-seal/shadow.json').unlink()
    with pytest.raises(SpendSealError, match='missing'):
        shadow.read()
    assert authoritative.read().tokens == 20
