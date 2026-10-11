"""WP-03 / RA-06, WP03.AC1: automatic personal-fact extraction is off unless deliberately enabled."""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import memory_policy  # noqa: E402


@pytest.mark.parametrize("cfg,expected", [
    (None, False),
    ({}, False),
    ({"harness": {}}, False),
    ({"harness": {"agentic": {"post_catchup_memory": {"enabled": True}}}}, False),   # legacy key, ignored
    ({"harness": {"pre_eviction_flush": {"enabled": True}}}, False),
    ({"harness": {"memory_extraction": {"allow_automatic": "true"}}}, False),        # not a real bool
    ({"harness": {"memory_extraction": {"allow_automatic": 1}}}, False),
    ({"harness": {"memory_extraction": {"allow_automatic": False}}}, False),
    ({"harness": {"memory_extraction": {"allow_automatic": True}}}, True),
])
def test_policy_table(cfg, expected):
    assert memory_policy.extraction_allowed(cfg) is expected


def test_unreadable_config_never_enables(monkeypatch):
    import lib.config_loader as cl  # noqa: PLC0415

    def boom(*a, **k):
        raise RuntimeError("config unreadable")
    monkeypatch.setattr(cl, "load_config", boom)
    assert memory_policy.extraction_allowed() is False


def test_malformed_config_shape_never_enables():
    assert memory_policy.extraction_allowed({"harness": "oops"}) is False
    assert memory_policy.extraction_allowed({"harness": {"memory_extraction": ["x"]}}) is False


# ── call sites ───────────────────────────────────────────────────────────────

def _install_fake_fact_extractor(monkeypatch):
    calls = {"persist": 0, "extract": 0}
    mod = types.ModuleType("fact_extractor")

    def extract_facts_from_summary(*a, **k):
        calls["extract"] += 1
        return ["fact"]

    def persist_facts(*a, **k):
        calls["persist"] += 1
        return 1
    mod.extract_facts_from_summary = extract_facts_from_summary
    mod.persist_facts = persist_facts
    monkeypatch.setitem(sys.modules, "fact_extractor", mod)
    return calls


def _catchup_summary():
    return types.SimpleNamespace(command_executed="catch-up")


def _history(tmp_path):
    tmp = tmp_path / "tmp"
    tmp.mkdir(exist_ok=True)
    (tmp / "session_history_1.md").write_text("# session\n")


def test_session_summarizer_does_not_extract_or_persist_when_disabled(tmp_path, monkeypatch):
    calls = _install_fake_fact_extractor(monkeypatch)
    _history(tmp_path)
    monkeypatch.setattr(memory_policy, "extraction_allowed", lambda *a, **k: False)
    import session_summarizer as ss  # noqa: PLC0415
    assert ss._auto_extract_facts_if_catchup(_catchup_summary(), tmp_path) == 0
    assert calls == {"persist": 0, "extract": 0}


def test_session_summarizer_still_persists_when_explicitly_enabled(tmp_path, monkeypatch):
    calls = _install_fake_fact_extractor(monkeypatch)
    _history(tmp_path)
    monkeypatch.setattr(memory_policy, "extraction_allowed", lambda *a, **k: True)
    import session_summarizer as ss  # noqa: PLC0415
    assert ss._auto_extract_facts_if_catchup(_catchup_summary(), tmp_path) == 1
    assert calls["persist"] == 1


def test_bootstrap_memory_refuses_when_disabled(tmp_path, monkeypatch, capsys):
    calls = _install_fake_fact_extractor(monkeypatch)
    monkeypatch.setattr(memory_policy, "extraction_allowed", lambda *a, **k: False)
    import bootstrap_memory as bm  # noqa: PLC0415
    (tmp_path / "briefings").mkdir()
    (tmp_path / "briefings" / "2026-10-01.md").write_text("# brief\n")
    monkeypatch.setattr(sys, "argv", ["bootstrap_memory.py", "--artha-dir", str(tmp_path)])
    rc = bm.main()
    assert rc == 0
    assert calls == {"persist": 0, "extract": 0}
    assert "disabled" in capsys.readouterr().err.lower()


def test_post_catchup_memory_is_disabled_by_default_and_on_config_failure(monkeypatch, tmp_path):
    pcm = pytest.importorskip("post_catchup_memory", reason="local-only script (untracked)")
    import lib.config_loader as cl  # noqa: PLC0415

    def boom(*a, **k):
        raise RuntimeError("unreadable")
    monkeypatch.setattr(cl, "load_config", boom)
    assert pcm._is_enabled(tmp_path) is False
    monkeypatch.setattr(cl, "load_config", lambda *_a, **_k: {
        "harness": {"agentic": {"post_catchup_memory": {"enabled": True}}}})
    assert pcm._is_enabled(tmp_path) is False, "the legacy explicit enabled: true no longer enables it"


def test_persist_facts_is_gated_at_the_choke_point(tmp_path, monkeypatch):
    fe = pytest.importorskip("fact_extractor", reason="local-only script (untracked)")
    monkeypatch.setattr(memory_policy, "extraction_allowed", lambda *a, **k: False)
    assert fe.persist_facts([object()], tmp_path) == 0
    assert not (tmp_path / "state" / "memory.md").exists()
