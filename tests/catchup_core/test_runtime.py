from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import yaml

from catchup_core import runtime as rt
from catchup_core.contracts import CatchupRequest, CoverageStatus, ExecutionMode, FailureClass


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, s, n):
        return self.store.get((s, n))

    def set_password(self, s, n, v):
        self.store[(s, n)] = v


@pytest.mark.parametrize('bundles', [
    {'school_email': None}, {'school_email': {'account_scope': ''}},
    {1: {'account_scope': 'remote'}}, {'school_email': {'account_scope': True}}])
def test_bundle_inventory_requires_resolved_source_and_account(bundles):
    config = rt.RuntimeConfig({}, ['kid1'], 'owner', policy={'bundles': bundles})
    with pytest.raises(rt.RuntimeConfigError, match='resolved'):
        rt._bundle_fetchers(config, None, FakeKeyring(), {}, lambda: None)


def test_runtime_inventory_uses_the_configured_bundle_account(tmp_path):
    repo = tmp_path / 'repo'
    (repo / 'state').mkdir(parents=True)
    write_cfg(repo, bundles={'school_email': {'account_scope': 'remote', 'producer': 'secondary',
        'recipient': 'authority', 'dir': str(tmp_path / 'bundles')}})
    deps = rt.build_deps(CatchupRequest('cu_inventory', 'ved', 'cli', mode=ExecutionMode.SHADOW),
        repo_root=repo, environ={'ARTHA_LOCAL_DIR': str(tmp_path / 'private')}, keyring_backend=FakeKeyring(),
        handler_loader=lambda _: None, auth_loader=lambda _: {})
    try:
        assert deps.expected_scopes['school_email'] == 'remote'
        deps.ledger.grant_consent('adult1', 'adult1', 'school_email:remote', ['ingest', 'disclose', 'view'], 'ved')
        assert deps.fetchers['school_email']().account_scope == 'remote'
    finally:
        deps.ledger.close()


@pytest.mark.parametrize('service', ['artha-ledger', 'artha-ledger-ids'])
@pytest.mark.parametrize('lost', ['current_version', 'key.1'])
def test_existing_installation_never_reenrolls_missing_keys(tmp_path, service, lost):
    from catchup_core.crypto import KeyUnavailableError
    repo = tmp_path / 'repo'
    (repo / 'state').mkdir(parents=True)
    write_cfg(repo)
    backend = FakeKeyring()
    def build():
        return rt.build_deps(CatchupRequest('key-loss', 'ved', 'cli', mode=ExecutionMode.SHADOW),
            repo_root=repo, environ={'ARTHA_LOCAL_DIR': str(tmp_path / 'local')}, keyring_backend=backend,
            handler_loader=lambda _: None, auth_loader=lambda _: {})
    build().ledger.close()
    del backend.store[service, lost]
    before = dict(backend.store)
    with pytest.raises(KeyUnavailableError):
        build()
    assert backend.store == before


def write_cfg(root, **over):
    (root / "config").mkdir(exist_ok=True)
    data = {"sources": {"school_email": {"connector": "gmail", "account_scope": "personal",
                                         "kwargs": {"query": "label:school"}},
                        "calendar": {"connector": "google_calendar", "account_scope": "family"}},
            "subjects": ["kid1", "kid2", "adult1"], "consent_required": ["adult1"], "viewer": "ved"}
    data.update(over)
    (root / "config" / "catchup_core.yaml").write_text(yaml.safe_dump(data))


@pytest.mark.parametrize('configured', [False, True])
def test_real_runtime_requires_explicit_resource_policy_before_disclosure(tmp_path, configured):
    from catchup_core.resources import AdmissionStore
    repo = tmp_path / 'repo'
    (repo / 'state').mkdir(parents=True)
    limits = dict(max_requests_per_bundle=2, max_prompt_bytes_per_bundle=50000,
        max_requests_per_catchup=10, max_requests_per_month=20, max_attempts_per_signal=2,
        max_pending_signals=5, max_priority_streak=2)
    write_cfg(repo, **({'resource_limits': limits} if configured else {}))
    deps = rt.build_deps(CatchupRequest('bounded-runtime', 'ved', 'cli', mode=ExecutionMode.SHADOW),
        repo_root=repo, environ={'ARTHA_LOCAL_DIR': str(tmp_path / 'private')}, keyring_backend=FakeKeyring(),
        handler_loader=lambda _: None, auth_loader=lambda _: {})
    try:
        assert deps.require_resource_policy is True
        assert isinstance(deps.admissions, AdmissionStore) is configured
        if configured:
            assert deps.admissions.policy.max_requests_per_month == 20
    finally:
        deps.ledger.close()


def test_reviewed_lifecycle_commands_have_explicit_cli_arguments():
    from catchup_core.cli import parse_args
    field = parse_args(['field-review', 'f1', '--apply', 'exact-token', '--reopen', '--shadow'])
    assert field.fact_id == 'f1' and field.apply == 'exact-token' and field.reopen
    retention = parse_args(['retention', '--at', '2026-10-24T12:00:00Z', '--apply', 'exact-digest'])
    assert retention.at == '2026-10-24T12:00:00Z' and retention.apply == 'exact-digest'
    assert parse_args(['cancel-extraction', 'cu-original', '--shadow']).catchup_id == 'cu-original'


def test_config_validation(tmp_path):
    p = tmp_path / "c.yaml"
    for bad in ["", "sources: {}", "sources: {a: {connector: x}}\nsubjects: [k]",
                "sources: {a: {connector: x, account_scope: y}}\nsubjects: []",
                "sources: {a: {connector: x, account_scope: y}}\nsubjects: [1]"]:
        p.write_text(bad)
        with pytest.raises(rt.RuntimeConfigError):
            rt.load_runtime_config(p)
    with pytest.raises(rt.RuntimeConfigError):
        rt.load_runtime_config(tmp_path / "missing.yaml")
    p.write_text("sources: {a: {connector: x, account_scope: y}}\nsubjects: [k]\n")
    cfg = rt.load_runtime_config(p)
    assert cfg.viewer == "owner" and cfg.subjects == ["k"] and cfg.consent_required == frozenset()


def test_consent_required_is_validated_and_reaches_the_ledger(tmp_path):
    p = tmp_path / "c.yaml"
    base = "sources: {a: {connector: x, account_scope: y}}\nsubjects: [k, adult]\n"
    p.write_text(base + "consent_required: [adult]\n")
    assert rt.load_runtime_config(p).consent_required == frozenset({"adult"})
    for bad in ("consent_required: adult\n", "consent_required: [stranger]\n", "consent_required: [1]\n"):
        p.write_text(base + bad)
        with pytest.raises(rt.RuntimeConfigError):
            rt.load_runtime_config(p)


def test_command_extractor_fails_closed():
    with pytest.raises(RuntimeError, match="no extractor command"):
        rt.command_extractor(None)("p")

    def ok(argv, **kw):
        assert kw["input"] == "PROMPT" and "-I" in argv
        return SimpleNamespace(returncode=0, stdout='{"complete": true, "candidates": []}')
    assert rt.command_extractor("python3 scripts/catchup_core/extractor_cmd.py", runner=ok)("PROMPT") == '{"complete": true, "candidates": []}'

    def bad(argv, **kw):
        return SimpleNamespace(returncode=2, stdout="")
    with pytest.raises(RuntimeError, match="exited 2"):
        rt.command_extractor("python3 scripts/catchup_core/extractor_cmd.py", runner=bad)("p")


def test_fetchers_degrade_when_loading_or_auth_fails():
    cfg = rt.RuntimeConfig({"school_email": {"connector": "gmail", "account_scope": "p"}}, ["k"], "o")

    def broken_loader(name):
        raise ImportError("not allowlisted")
    f = rt.build_fetchers(cfg, "2026-10-01T00:00:00Z", broken_loader, lambda n: {}, {"school_email": True})
    got = f["school_email"]()
    assert got.status is CoverageStatus.UNAVAILABLE and got.error is FailureClass.AUTH and got.required


def test_fetchers_use_handler_and_kwargs():
    seen = {}

    class Handler:
        @staticmethod
        def fetch(**kw):
            seen.update(kw)
            yield {"id": "m1", "source": "school_email", "date_iso": "2026-10-11", "subject": "s", "body": "b"}
    cfg = rt.RuntimeConfig({"school_email": {"connector": "gmail", "account_scope": "p",
                                             "kwargs": {"query": "label:school"}}}, ["k"], "o")
    f = rt.build_fetchers(cfg, "2026-10-01T00:00:00Z", lambda n: Handler, lambda n: {"token": "t"},
                          {"school_email": False})
    got = f["school_email"]()
    assert got.status is CoverageStatus.COMPLETE and len(got.records) == 1 and not got.required
    assert seen["query"] == "label:school" and seen["auth_context"] == {"token": "t"}


def test_verified_scope_fetch_uses_overlap_and_work_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path / 'private-coverage'))
    from test_user_surfaces import CIPHER, KEY
    from catchup_core.ledger import Ledger
    seen = {}
    class Handler:
        @staticmethod
        def fetch(**kw):
            seen.update(kw)
            return iter(())
    with Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER).open() as led:
        led.migrate()
        led.set_coverage("school_email", "personal", "complete", completed_at="2026-10-11T10:00:00Z")
        spec = {"connector": "gmail", "account_scope": "personal"}
        cfg = rt.RuntimeConfig({"school_email": spec}, ["kid1"], "owner")
        fetch = rt.build_fetchers(cfg, "2026-01-01T00:00:00Z", lambda _: Handler, lambda _: {}, {}, led)
        assert fetch["school_email"]().status is CoverageStatus.COMPLETE
        assert seen["since"] == "2026-10-10T10:00:00+00:00"
        cfg.sources["school_email"]["namespace"] = "work"
        assert fetch["school_email"]().status is CoverageStatus.UNAVAILABLE


def test_unknown_requested_pack_and_private_storage_in_repo_are_refused(tmp_path):
    with pytest.raises(rt.RuntimeConfigError, match="one or more"):
        rt._load_packs(("kids_school", "missing_pack"))
    repo = tmp_path / "repo"
    repo.mkdir()
    write_cfg(repo)
    request = CatchupRequest("cu", "ved", "cli", mode=ExecutionMode.SHADOW)
    with pytest.raises(rt.RuntimeConfigError, match="outside"):
        rt.build_deps(request, repo_root=repo, environ={"ARTHA_LOCAL_DIR": str(repo / "private")})


def test_inspection_without_a_prior_catchup_does_not_create_keys(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_cfg(repo)
    keys = FakeKeyring()
    with pytest.raises(rt.RuntimeConfigError, match="no Catch-up"):
        rt.build_deps(CatchupRequest("cu", "ved", "cli", mode=ExecutionMode.SHADOW), repo_root=repo,
            environ={"ARTHA_LOCAL_DIR": str(tmp_path / "local")}, keyring_backend=keys, inspection=True)
    assert not keys.store


def test_build_deps_wires_local_storage_keys_and_shadow_safety(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "state").mkdir(parents=True)
    write_cfg(repo)
    (repo / "state" / "finance.md").write_text("---\ndomain: finance\nlast_updated: '2026-10-01'\n---\n")
    local = tmp_path / "local"
    request = CatchupRequest("cu_1", "ved", "cli", mode=ExecutionMode.SHADOW)
    kr = FakeKeyring()
    deps = rt.build_deps(
        request, repo_root=repo, environ={"ARTHA_LOCAL_DIR": str(local), "ARTHA_EXTRACTOR_CMD": "model"},
        keyring_backend=kr, handler_loader=lambda n: SimpleNamespace(fetch=lambda **kw: iter(())),
        auth_loader=lambda n: {}, now=lambda: datetime(2026, 10, 12, tzinfo=timezone.utc))
    try:
        assert (local / "catchup_core" / "shadow" / "ledger.db").exists()
        assert deps.pack.domain == "kids_school" and set(deps.fetchers) == {
            "school_email", "calendar", "capture", "documents"}
        assert deps.ledger.schema_version() >= 2 and deps.viewer == "ved"
        assert deps.ledger._consent_subjects == frozenset({"adult1"})   # the gate is actually armed
        assert deps.expected_scopes == {'school_email': 'personal', 'calendar': 'family',
                                        'capture': 'ved', 'documents': 'manual'}
        keys = {n for (_s, n) in kr.store}
        assert {"key.1", "current_version"} <= keys                       # keys live in the credential store
        out = deps.incumbent.run([])
        assert [c.domain for c in out.conclusions] == ["finance"]
        # a full shadow run with nothing configured to fetch still completes honestly
        from catchup_core.coordinator import run_catchup
        # Exercise runtime checks against synthetic inventory, never the developer's scheduler.
        monkeypatch.setattr("preflight.footprint_checks.check_background_footprint",
                            lambda: SimpleNamespace(passed=True))
        result = run_catchup(request, deps)
        assert result.briefing_id is None and result.mode is ExecutionMode.SHADOW
        assert '0/2 required source scopes complete this Catch-up' in result.briefing_text
    finally:
        deps.ledger.close()


def test_missing_pack_or_config_is_a_config_error(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    request = CatchupRequest("cu", "u", "c", mode=ExecutionMode.SHADOW)
    with pytest.raises(rt.RuntimeConfigError):
        rt.build_deps(request, repo_root=repo, environ={"ARTHA_LOCAL_DIR": str(tmp_path / "l")})
    write_cfg(repo)
    with pytest.raises(rt.RuntimeConfigError):
        rt.build_deps(CatchupRequest("cu", "ved", "cli", mode=ExecutionMode.SHADOW, pilot_domains=("nope",)),
                      repo_root=repo, environ={"ARTHA_LOCAL_DIR": str(tmp_path / "l")}, keyring_backend=FakeKeyring())


def test_default_loaders_use_the_incumbent_allowlist_and_auth(monkeypatch):
    import sys
    import types
    fake_pipeline = types.ModuleType("pipeline")
    fake_pipeline.load_connectors_config = lambda: {"connectors": {"gmail": {"fetch": {"handler": "connectors.google_email"}}}}
    fake_pipeline._normalize_connectors = lambda cfg: [{"name": "gmail", **cfg["connectors"]["gmail"]}]
    loaded = []
    fake_pipeline._load_handler = lambda path: loaded.append(path) or "HANDLER"
    fake_auth = types.ModuleType("lib.auth")
    fake_auth.load_auth_context = lambda conn: {"for": conn["name"]}
    monkeypatch.setitem(sys.modules, "pipeline", fake_pipeline)
    monkeypatch.setitem(sys.modules, "lib.auth", fake_auth)
    handler, auth = rt._default_loaders()
    assert handler("gmail") == "HANDLER" and loaded == ["connectors.google_email"]
    assert auth("gmail") == {"for": "gmail"}


def test_backup_callback_copies_the_database(tmp_path):
    import sqlite3
    from contextlib import closing
    db = tmp_path / "ledger.db"
    with closing(sqlite3.connect(db)) as c:
        c.execute("CREATE TABLE example(x)")
        c.execute("INSERT INTO example VALUES(42)")
        c.commit()
    dest = rt._backup_for(tmp_path)(db)
    with closing(sqlite3.connect(dest)) as c:
        assert c.execute("SELECT x FROM example").fetchone()[0] == 42
    assert dest.name.startswith("ledger.pre-migration.")


def test_stale_ledger_blocks_build(tmp_path):
    repo = tmp_path / "repo"
    (repo / "state").mkdir(parents=True)
    write_cfg(repo)
    local = tmp_path / "local"
    request = CatchupRequest("cu", "ved", "cli", mode=ExecutionMode.SHADOW)
    kr = FakeKeyring()
    env = {"ARTHA_LOCAL_DIR": str(local)}
    loaders = dict(handler_loader=lambda n: None, auth_loader=lambda n: {})
    deps = rt.build_deps(request, repo_root=repo, environ=env, keyring_backend=kr, **loaders)
    deps.ledger.forget("c", "a", "u1")                    # seals checkpoint 1
    deps.ledger.close()
    (local / "catchup_core" / "shadow" / "ledger.db").unlink()
    for suffix in ("-wal", "-shm"):
        (local / "catchup_core" / "shadow" / f"ledger.db{suffix}").unlink(missing_ok=True)
    from catchup_core.ledger import LedgerLocked
    with pytest.raises((rt.RuntimeConfigError, LedgerLocked)):
        rt.build_deps(request, repo_root=repo, environ=env, keyring_backend=kr, **loaders)


def test_build_deps_multiple_pilot_domains_creates_composite_pack(tmp_path):
    repo = tmp_path / "repo"
    (repo / "state").mkdir(parents=True)
    write_cfg(repo, sources={
        "school_email": {"connector": "gmail", "account_scope": "personal"},
        "calendar": {"connector": "google_calendar", "account_scope": "family"},
        "finance_email": {"connector": "gmail", "account_scope": "personal", "class": "email_notice"}})
    local = tmp_path / "local"
    request = CatchupRequest("cu_multi", "ved", "cli", mode=ExecutionMode.SHADOW,
                             pilot_domains=("kids_school", "finance"))
    kr = FakeKeyring()
    env = {"ARTHA_LOCAL_DIR": str(local)}
    loaders = dict(handler_loader=lambda n: None, auth_loader=lambda n: {})
    deps = rt.build_deps(request, repo_root=repo, environ=env, keyring_backend=kr, **loaders)
    try:
        assert deps.pack.domain == "kids_school+finance"
        assert "bill_due_date" in deps.pack.predicates
        assert "exam_registration_deadline" in deps.pack.predicates
    finally:
        deps.ledger.close()


def test_unclassified_source_is_rejected_before_any_storage_is_opened(tmp_path):
    """A source class no predicate ranks would make every Fact UNKNOWN: fail loudly instead."""
    repo = tmp_path / "repo"
    (repo / "state").mkdir(parents=True)
    write_cfg(repo)                      # no 'class' for the gmail source used by finance
    local = tmp_path / "local"
    request = CatchupRequest("cu_cls", "ved", "cli", mode=ExecutionMode.SHADOW,
                             pilot_domains=("kids_school", "finance"))
    with pytest.raises(rt.RuntimeConfigError, match="set 'class:'"):
        rt.build_deps(request, repo_root=repo, environ={"ARTHA_LOCAL_DIR": str(local)}, keyring_backend=FakeKeyring(),
                      handler_loader=lambda n: None, auth_loader=lambda n: {})
    assert not (local / "shadow" / "ledger.db").exists()


def test_implementation_fingerprint_includes_provider_and_credential_consumers(tmp_path):
    (tmp_path / 'scripts/lib').mkdir(parents=True)
    provider = tmp_path / 'scripts/google_auth.py'
    store = tmp_path / 'scripts/lib/credentials.py'
    provider.write_text('provider implementation one')
    store.write_text('credential implementation one')
    first = rt._implementation_fingerprint(tmp_path)
    provider.write_text('provider implementation two')
    second = rt._implementation_fingerprint(tmp_path)
    store.write_text('credential implementation two')
    third = rt._implementation_fingerprint(tmp_path)
    assert len({first, second, third}) == 3
    (tmp_path / 'unrelated.txt').write_text('unrelated data must not enter the implementation generation')
    assert rt._implementation_fingerprint(tmp_path) == third
    store.unlink()
    assert rt._implementation_fingerprint(tmp_path) != third


def test_implementation_generation_includes_migration_source(tmp_path):
    directory = tmp_path / 'scripts/catchup_core/migrations'
    directory.mkdir(parents=True)
    script = directory / '0001_initial.sql'
    script.write_text('CREATE TABLE example(a);')
    first = rt._implementation_fingerprint(tmp_path)
    script.write_text('CREATE TABLE example(b);')
    assert rt._implementation_fingerprint(tmp_path) != first


def test_composite_pack_conflicts_use_the_runtime_configuration_error_surface():
    with pytest.raises(rt.RuntimeConfigError, match='duplicate Domain'):
        rt._load_packs(('kids_school', 'kids_school'))


@pytest.mark.parametrize('change', ['config', 'profile', 'actions', 'pack', 'prompt', 'implementation', 'removed', 'unreadable'])
def test_runtime_inputs_changed_after_dependency_construction_require_a_fresh_catchup(tmp_path, monkeypatch, change):
    repo = tmp_path / 'repo'
    repo.mkdir()
    pack = rt._PACKS / 'kids_school.yaml'
    packs = tmp_path / 'packs'
    packs.mkdir()
    (packs / pack.name).write_bytes(pack.read_bytes())
    prompts = tmp_path / 'prompts'
    prompts.mkdir()
    monkeypatch.setattr(rt, '_PACKS', packs)
    monkeypatch.setattr(rt, '_PROMPTS', prompts)
    write_cfg(repo, subjects=['kid1'], consent_required=[])
    (repo / 'state').mkdir()
    request = CatchupRequest('cu_frozen', 'ved', 'cli', mode=ExecutionMode.SHADOW)
    deps = rt.build_deps(request, repo_root=repo, environ={'ARTHA_LOCAL_DIR': str(tmp_path / 'private-parent')},
        keyring_backend=FakeKeyring(), handler_loader=lambda _: None, auth_loader=lambda _: {})
    try:
        deps.validate_inputs()
        path = repo / 'config/catchup_core.yaml'
        if change == 'config':
            path.write_bytes(path.read_bytes() + b'\n# owner changed the policy\n')
        elif change in {'profile', 'actions'}:
            name = 'user_profile.yaml' if change == 'profile' else 'actions.yaml'
            (repo / 'config' / name).write_text('synthetic: changed\n')
        elif change == 'pack':
            path = packs / pack.name
            path.write_bytes(path.read_bytes() + b'\n# synthetic change\n')
        elif change == 'prompt':
            (prompts / 'synthetic.md').write_text('Synthetic prompt change')
        elif change == 'implementation':
            monkeypatch.setattr(rt, '_implementation_fingerprint', lambda _: 'synthetic-changed-code')
        elif change == 'removed':
            path.unlink()
        else:
            original = rt.Path.read_bytes
            def unreadable(candidate):
                if candidate == path:
                    raise OSError('synthetic-private-path')
                return original(candidate)
            monkeypatch.setattr(rt.Path, 'read_bytes', unreadable)
        with pytest.raises(rt.LedgerError, match='inputs (changed|unavailable)') as error:
            deps.validate_inputs()
        assert 'synthetic-private-path' not in str(error.value)
    finally:
        deps.ledger.close()


@pytest.mark.parametrize('field,value', [('cutover', 'false'), ('cutover', 1), ('household_from_profile', 'false'),
    ('viewer', 3), ('bootstrap_days', True), ('bootstrap_days', 1.5), ('bootstrap_days', -1),
    ('snapshot_retention_days', '30'), ('snapshot_retention_days', 0), ('extractor_cmd', True),
    ('domains', 'kids_school'), ('domains', ['../private']), ('subjects', ['kid1', 'kid1'])])
def test_runtime_policy_rejects_coercion_and_malformed_scope(tmp_path, field, value):
    write_cfg(tmp_path, **{field: value})
    with pytest.raises(rt.RuntimeConfigError):
        rt.load_runtime_config(tmp_path / 'config/catchup_core.yaml')


@pytest.mark.parametrize('value', [[], 'plain string', 3, True])
def test_runtime_config_requires_an_object(tmp_path, value):
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(value))
    with pytest.raises(rt.RuntimeConfigError):
        rt.load_runtime_config(path)


@pytest.mark.parametrize('field,value', [('connector', 3), ('account_scope', 3), ('kwargs', []),
    ('subjects', []), ('subjects', 'adult1'), ('subjects', ['kid1', 'kid1']), ('required', 'false')])
def test_runtime_source_policy_is_typed_before_any_fetch(tmp_path, field, value):
    spec = {'connector': 'gmail', 'account_scope': 'personal', field: value}
    write_cfg(tmp_path, sources={'school_email': spec})
    with pytest.raises(rt.RuntimeConfigError):
        rt.load_runtime_config(tmp_path / 'config/catchup_core.yaml')


def test_goals_authority_accepts_only_core_or_incumbent(tmp_path):
    path = tmp_path / 'config' / 'catchup_core.yaml'
    for value in ('core', 'incumbent'):
        write_cfg(tmp_path, goals_authority=value)
        assert rt.load_runtime_config(path).policy['goals_authority'] == value
    write_cfg(tmp_path, goals_authority='shared')
    with pytest.raises(rt.RuntimeConfigError, match='goals_authority'):
        rt.load_runtime_config(path)


def test_goals_kept_by_the_incumbent_are_disclosed_but_do_not_degrade_the_run():
    goals, plans = rt._goals_held_by_incumbent(), rt._no_goal_plans()
    assert goals.gaps == () and plans.gaps == ()
    assert goals.lines and 'incumbent' in goals.lines[0] and 'does not read' in goals.lines[0]
