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


def test_bootstrap_memory_reports_missing_or_empty_briefings(tmp_path, monkeypatch, capsys):
    import bootstrap_memory as bm  # noqa: PLC0415

    calls = _install_fake_fact_extractor(monkeypatch)
    monkeypatch.setattr(memory_policy, "extraction_allowed", lambda *a, **k: True)
    monkeypatch.setattr(sys, "argv", ["bootstrap_memory.py", "--artha-dir", str(tmp_path)])
    assert bm.main() == 1
    assert "briefings/ directory not found" in capsys.readouterr().err.lower()

    (tmp_path / "briefings").mkdir()
    assert bm.main() == 0
    assert "no briefing files found" in capsys.readouterr().err.lower()
    assert calls == {"persist": 0, "extract": 0}


def test_bootstrap_memory_deduplicates_before_single_persist(tmp_path, monkeypatch, capsys):
    import bootstrap_memory as bm  # noqa: PLC0415

    calls = _install_fake_fact_extractor(monkeypatch)
    monkeypatch.setattr(memory_policy, "extraction_allowed", lambda *a, **k: True)
    monkeypatch.setattr(bm, "_seed_procedures", lambda *_a, **_k: 1)
    extractor = sys.modules["fact_extractor"]
    fact = types.SimpleNamespace(id="shared", type="type", domain="test", statement="statement")

    def extract(*_a, **_k):
        calls["extract"] += 1
        return [fact]

    extractor.extract_facts_from_summary = extract
    briefings = tmp_path / "briefings"
    briefings.mkdir()
    (briefings / "2026-10-01.md").write_text("# first\n")
    (briefings / "2026-10-02.md").write_text("# second\n")
    monkeypatch.setattr(sys, "argv", ["bootstrap_memory.py", "--artha-dir", str(tmp_path)])

    assert bm.main() == 0
    assert calls == {"persist": 1, "extract": 2}
    assert "Seeded 1 procedure(s)" in capsys.readouterr().out


@pytest.mark.parametrize("dry_run", [False, True])
def test_bootstrap_memory_fails_when_a_briefing_extraction_fails(
    tmp_path, monkeypatch, capsys, dry_run
):
    import bootstrap_memory as bm  # noqa: PLC0415

    calls = _install_fake_fact_extractor(monkeypatch)
    monkeypatch.setattr(memory_policy, "extraction_allowed", lambda *a, **k: True)
    extractor = sys.modules["fact_extractor"]

    def extract(path, *_a, **_k):
        if path.name == "2026-10-02.md":
            raise OSError("synthetic read failure")
        calls["extract"] += 1
        return [types.SimpleNamespace(id="fact", type="type", domain="test", statement="statement")]

    extractor.extract_facts_from_summary = extract
    monkeypatch.setattr(bm, "_seed_procedures", lambda *_a, **_k: 0)
    briefings = tmp_path / "briefings"
    briefings.mkdir()
    (briefings / "2026-10-01.md").write_text("# first\n")
    (briefings / "2026-10-02.md").write_text("# second\n")
    argv = ["bootstrap_memory.py", "--artha-dir", str(tmp_path)]
    if dry_run:
        argv.append("--dry-run")
    monkeypatch.setattr(sys, "argv", argv)

    assert bm.main() == 1
    assert calls == {"persist": int(not dry_run), "extract": 1}
    assert "extraction failed" in capsys.readouterr().err.lower()


def test_bootstrap_procedure_seed_write_failure_propagates(tmp_path, monkeypatch):
    import bootstrap_memory as bm  # noqa: PLC0415

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "bootstrap_seeds.yaml").write_text(
        "procedures:\n  - domain: test\n    trigger_slug: sample\n    content: example\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bm, "_SCRIPTS_DIR", scripts)
    write_text = Path.write_text

    def fail_seed_write(path, *args, **kwargs):
        if path.name == "test-sample.md":
            raise OSError("synthetic write failure")
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_seed_write)
    with pytest.raises(OSError, match="synthetic write failure"):
        bm._seed_procedures(tmp_path)


def test_bootstrap_procedure_seeding_is_idempotent_and_dry_run_is_read_only(tmp_path, monkeypatch, capsys):
    import bootstrap_memory as bm  # noqa: PLC0415

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    seeds_path = scripts / "bootstrap_seeds.yaml"
    seeds_path.write_text(
        "procedures:\n"
        "  - domain: test\n"
        "    trigger_slug: sample\n"
        "    content: example\n"
        "  - domain: test\n"
        "    trigger_slug: empty\n"
        "    content: ''\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bm, "_SCRIPTS_DIR", scripts)
    artha_dir = tmp_path / "artha"

    assert bm._seed_procedures(artha_dir) == 1
    dest = artha_dir / "state" / "learned_procedures" / "test-sample.md"
    assert dest.read_text(encoding="utf-8") == "example\n"
    assert bm._seed_procedures(artha_dir) == 0

    seeds_path.write_text(
        "procedures:\n  - domain: test\n    trigger_slug: dry\n    content: preview\n",
        encoding="utf-8",
    )
    assert bm._seed_procedures(artha_dir, dry_run=True) == 1
    assert "would write test-dry.md" in capsys.readouterr().out
    assert not (dest.parent / "test-dry.md").exists()


def test_bootstrap_procedure_seeding_skips_missing_seed_file(tmp_path, monkeypatch):
    import bootstrap_memory as bm  # noqa: PLC0415

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    monkeypatch.setattr(bm, "_SCRIPTS_DIR", scripts)
    assert bm._seed_procedures(tmp_path) == 0


@pytest.mark.parametrize(
    ("domain", "trigger_slug"),
    [("../..", "escape"), ("test", "../../escape")],
)
def test_bootstrap_procedure_seed_rejects_path_components(
    tmp_path, monkeypatch, domain, trigger_slug
):
    import bootstrap_memory as bm  # noqa: PLC0415

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "bootstrap_seeds.yaml").write_text(
        "procedures:\n"
        f"  - domain: '{domain}'\n"
        f"    trigger_slug: '{trigger_slug}'\n"
        "    content: example\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bm, "_SCRIPTS_DIR", scripts)
    artha_dir = tmp_path / "artha"
    (artha_dir / "state").mkdir(parents=True)

    with pytest.raises(ValueError, match="Invalid procedure"):
        bm._seed_procedures(artha_dir)

    assert not list(artha_dir.rglob("*.md"))


def test_bootstrap_procedure_seed_rejects_directory_symlink_escape(tmp_path, monkeypatch):
    import bootstrap_memory as bm  # noqa: PLC0415

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "bootstrap_seeds.yaml").write_text(
        "procedures:\n"
        "  - domain: test\n"
        "    trigger_slug: sample\n"
        "    content: example\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bm, "_SCRIPTS_DIR", scripts)
    artha_dir = tmp_path / "artha"
    proc_dir = artha_dir / "state" / "learned_procedures"
    proc_dir.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        proc_dir.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation unavailable: {exc}")

    with pytest.raises(ValueError, match="Procedure directory"):
        bm._seed_procedures(artha_dir)

    assert not (outside / "test-sample.md").exists()


def test_bootstrap_procedure_seed_rejects_file_symlink_escape(tmp_path, monkeypatch):
    import bootstrap_memory as bm  # noqa: PLC0415

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "bootstrap_seeds.yaml").write_text(
        "procedures:\n"
        "  - domain: test\n"
        "    trigger_slug: sample\n"
        "    content: example\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bm, "_SCRIPTS_DIR", scripts)
    artha_dir = tmp_path / "artha"
    proc_dir = artha_dir / "state" / "learned_procedures"
    proc_dir.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    dest = proc_dir / "test-sample.md"
    try:
        dest.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlink creation unavailable: {exc}")

    with pytest.raises(ValueError, match="Procedure path"):
        bm._seed_procedures(artha_dir)

    assert not outside.exists()


def test_self_model_rebuild_failure_returns_nonzero(monkeypatch, tmp_path, capsys):
    import post_catchup_memory as pcm  # noqa: PLC0415
    import self_model_writer as smw  # noqa: PLC0415

    class FailingWriter:
        def update(self, **_kwargs):
            raise OSError("synthetic self-model write failure")

    monkeypatch.setattr(smw, "SelfModelWriter", FailingWriter)
    monkeypatch.setattr(
        sys, "argv",
        ["post_catchup_memory.py", "--rebuild-self-model", "--artha-dir", str(tmp_path)],
    )

    assert pcm.main() == 2
    assert "Self-model rebuild failed" in capsys.readouterr().err


@pytest.mark.parametrize("updated", [True, False])
def test_self_model_rebuild_success_status(monkeypatch, tmp_path, capsys, updated):
    import post_catchup_memory as pcm  # noqa: PLC0415
    import self_model_writer as smw  # noqa: PLC0415

    class Writer:
        def update(self, **_kwargs):
            return updated

    monkeypatch.setattr(smw, "SelfModelWriter", Writer)
    monkeypatch.setattr(
        sys, "argv",
        ["post_catchup_memory.py", "--rebuild-self-model", "--artha-dir", str(tmp_path)],
    )

    assert pcm.main() == 0
    output = capsys.readouterr().out
    assert ("Self-model rebuilt." if updated else "Self-model unchanged.") in output


@pytest.mark.parametrize("existing", ["alerts: [", "- not-a-mapping", "{}"])
def test_eval_alert_write_preserves_invalid_existing_history(tmp_path, existing):
    import post_catchup_memory as pcm  # noqa: PLC0415

    alerts_path = tmp_path / "state" / "eval_alerts.yaml"
    alerts_path.parent.mkdir()
    alerts_path.write_text(existing, encoding="utf-8")
    original = alerts_path.read_bytes()

    with pytest.raises(ValueError, match="Existing evaluation alerts"):
        pcm._write_eval_alerts([{"code": "synthetic", "connector": "test"}], tmp_path)

    assert alerts_path.read_bytes() == original


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
