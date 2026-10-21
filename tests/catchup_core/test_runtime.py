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


def write_cfg(root, **over):
    (root / "config").mkdir(exist_ok=True)
    data = {"sources": {"school_email": {"connector": "gmail", "account_scope": "personal",
                                         "kwargs": {"query": "label:school"}},
                        "calendar": {"connector": "google_calendar", "account_scope": "family"}},
            "subjects": ["kid1", "kid2", "adult1"], "consent_required": ["adult1"], "viewer": "ved"}
    data.update(over)
    (root / "config" / "catchup_core.yaml").write_text(yaml.safe_dump(data))


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
        assert kw["input"] == "PROMPT" and argv == ["model", "--json"]
        return SimpleNamespace(returncode=0, stdout='{"candidates": []}')
    assert rt.command_extractor("model --json", runner=ok)("PROMPT") == '{"candidates": []}'

    def bad(argv, **kw):
        return SimpleNamespace(returncode=2, stdout="")
    with pytest.raises(RuntimeError, match="exited 2"):
        rt.command_extractor("model", runner=bad)("p")


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
        assert (local / "catchup_core" / "ledger.db").exists()
        assert deps.pack.domain == "kids_school" and set(deps.fetchers) == {"school_email", "calendar"}
        assert deps.ledger.schema_version() >= 2 and deps.viewer == "ved"
        assert deps.ledger._consent_subjects == frozenset({"adult1"})   # the gate is actually armed
        keys = {n for (_s, n) in kr.store}
        assert {"key.1", "current_version"} <= keys                       # keys live in the credential store
        out = deps.incumbent.run([])
        assert [c.domain for c in out.conclusions] == ["finance"]
        # a full shadow run with nothing configured to fetch still completes honestly
        from catchup_core.coordinator import run_catchup
        result = run_catchup(request, deps)
        assert result.briefing_id is None and result.mode is ExecutionMode.SHADOW
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
        rt.build_deps(CatchupRequest("cu", "u", "c", mode=ExecutionMode.SHADOW, pilot_domains=("nope",)),
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
    db = tmp_path / "ledger.db"
    db.write_bytes(b"sqlite")
    dest = rt._backup_for(tmp_path)(db)
    assert dest.read_bytes() == b"sqlite" and dest.name.startswith("ledger.pre-migration.")


def test_coverage_line_never_raises(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "lib.connector_coverage", None)
    assert rt._coverage_line() == "Coverage: not summarized."


def test_stale_ledger_blocks_build(tmp_path):
    repo = tmp_path / "repo"
    (repo / "state").mkdir(parents=True)
    write_cfg(repo)
    local = tmp_path / "local"
    request = CatchupRequest("cu", "u", "c", mode=ExecutionMode.SHADOW)
    kr = FakeKeyring()
    env = {"ARTHA_LOCAL_DIR": str(local)}
    loaders = dict(handler_loader=lambda n: None, auth_loader=lambda n: {})
    deps = rt.build_deps(request, repo_root=repo, environ=env, keyring_backend=kr, **loaders)
    deps.ledger.forget("c", "a", "u1")                    # seals checkpoint 1
    deps.ledger.close()
    (local / "catchup_core" / "ledger.db").unlink()        # an old/empty ledger replaces it
    for suffix in ("-wal", "-shm"):
        (local / "catchup_core" / f"ledger.db{suffix}").unlink(missing_ok=True)
    with pytest.raises(rt.RuntimeConfigError, match="sealed deletion checkpoint"):
        rt.build_deps(request, repo_root=repo, environ=env, keyring_backend=kr, **loaders)


def test_build_deps_multiple_pilot_domains_creates_composite_pack(tmp_path):
    repo = tmp_path / "repo"
    (repo / "state").mkdir(parents=True)
    write_cfg(repo)
    local = tmp_path / "local"
    request = CatchupRequest("cu_multi", "u", "c", mode=ExecutionMode.SHADOW,
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
