"""WP-02: preflight separates capability (can I run?) from coverage (what did the last fetch deliver?)."""
from __future__ import annotations

import json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

from preflight.coverage_checks import check_connector_coverage  # noqa: E402


def _write(tmp_path, connectors):
    path = tmp_path / "cov.json"
    path.write_text(json.dumps({"connectors": connectors}))
    return path


def _entry(status, records=0, **extra):
    return {"last_run": {"status": status, "records": records, "rejected": extra.pop("rejected", 0),
                         "error": extra.pop("error", None), "at": "2026-10-11T00:00:00Z"}, **extra}


def test_no_run_recorded_is_not_a_failure(tmp_path):
    res = check_connector_coverage(tmp_path / "missing.json")
    assert res.passed and res.severity == "P1"
    assert "no fetch recorded" in res.message


def test_valid_empty_calendar_passes(tmp_path):
    path = _write(tmp_path, {"google_calendar": _entry("complete", 0), "gmail": _entry("complete", 40)})
    res = check_connector_coverage(path)
    assert res.passed, res.message


def test_rejected_records_fail_the_check(tmp_path):
    path = _write(tmp_path, {"google_calendar": _entry("degraded", 0, rejected=12)})
    res = check_connector_coverage(path)
    assert not res.passed
    assert "google_calendar" in res.message and "degraded" in res.message
    assert res.severity == "P1", "coverage degrades a scope; it never blocks globally"


def test_failed_auth_fails_the_check(tmp_path):
    path = _write(tmp_path, {"outlook_calendar": _entry("unavailable", 0, error="invalid_grant")})
    res = check_connector_coverage(path)
    assert not res.passed and "outlook_calendar" in res.message


def test_volume_anomaly_fails_the_check(tmp_path):
    path = _write(tmp_path, {"google_calendar": _entry("anomaly", 0)})
    res = check_connector_coverage(path)
    assert not res.passed and "usually returns" in res.message


def test_chronic_failure_age_is_reported(tmp_path):
    path = _write(tmp_path, {"google_calendar": _entry("unavailable", 0, error="x",
                                                       failing_since="2026-08-30T00:00:00Z")})
    res = check_connector_coverage(path, now="2026-10-11T00:00:00Z")
    assert "42 days" in res.message


# ── plaintext fetched content in the synced workspace (P1) ──────────────────

from preflight.coverage_checks import check_plaintext_in_sync_root  # noqa: E402


def test_clean_workspace_passes(tmp_path):
    (tmp_path / "tmp").mkdir()
    assert check_plaintext_in_sync_root(tmp_path).passed


def test_leftover_pipeline_output_is_flagged(tmp_path):
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "pipeline_output.jsonl").write_text('{"body": "x"}\n')
    res = check_plaintext_in_sync_root(tmp_path)
    assert not res.passed and res.severity == "P1"
    assert "pipeline_output.jsonl" in res.message
    assert "~/.artha-local" in res.fix_hint


def test_sync_conflict_copy_of_vault_marker_is_flagged(tmp_path):
    (tmp_path / "-Some Host.artha-decrypted").write_text("{}")
    assert not check_plaintext_in_sync_root(tmp_path).passed


# ── eval alerts, health-check compaction, planning signals & briefings archive ──

def _load_cli_preflight(monkeypatch, name: str):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(name, SCRIPTS / "preflight.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_unacked_p0_and_invalid_eval_alerts_fail(tmp_path, monkeypatch):
    import pytest
    import yaml

    pf_cli = _load_cli_preflight(monkeypatch, "preflight_eval")
    monkeypatch.setattr(pf_cli, "ARTHA_DIR", str(tmp_path))
    alerts_file = tmp_path / "eval_alerts.yaml"
    alerts_file.write_text(yaml.dump({
        "alerts": [{"code": "EV-SH-01", "severity": "P0", "message": "Quality scorer crash", "acked": False}],
    }), encoding="utf-8")
    p0_res = pf_cli.check_eval_alerts()
    assert p0_res.ok is False and p0_res.severity == "P0"

    for bad in ({}, {"alerts": "not-a-list"}, {"alerts": [None]}, {"alerts": None}):
        alerts_file.write_text(yaml.safe_dump(bad), encoding="utf-8")
        bad_res = pf_cli.check_eval_alerts()
        assert bad_res.ok is False and bad_res.severity == "P1" and "invalid" in bad_res.message.lower()
    assert pytest  # imported only if needed later


def test_health_check_template_and_compaction_satisfy_schema(tmp_path, monkeypatch):
    import yaml
    from lib.state_schema import validate_frontmatter
    import lib.state_writer as state_writer

    root = SCRIPTS.parent
    tmpl = (root / "state" / "templates" / "health-check.md").read_text(encoding="utf-8")
    assert validate_frontmatter("health-check.md", yaml.safe_load(tmpl.split("---\n")[1]) or {}) == []

    cli = _load_cli_preflight(monkeypatch, "preflight_cli_under_test")
    state = tmp_path / "state"
    state.mkdir()
    runs = "".join(f"### run {i}\n- ok\n" for i in range(40))
    (state / "health-check.md").write_text(
        "---\nschema_version: '1.1'\nlast_catch_up: never\ncatch_up_count: 0\n---\n\n"
        "## Catch-Up Run History\n\n" + runs,
        encoding="utf-8",
    )
    written: dict[str, str] = {}
    monkeypatch.setattr(cli, "ARTHA_DIR", str(tmp_path))
    monkeypatch.setattr(state_writer, "write", lambda p, content, **kw: written.update(content=content, domain=kw["domain"]))
    cli._compact_health_check()

    fm = yaml.safe_load(written["content"].split("---\n")[1]) or {}
    assert validate_frontmatter("health-check.md", fm) == []
    assert fm["domain"] == "health-check" and fm["catch_up_count"] == 0
    assert written["content"].count("### run ") == 20


def _write_planning_signals(path: Path, last_updated: str | None) -> None:
    updated = f'last_updated: "{last_updated}"\n' if last_updated is not None else ""
    path.write_text(
        "---\nschema_version: 1\ndomain: planning_signals\n" + updated +
        "signals:\n  - id: SIG-001\n    entity_key: synthetic_opportunity\n"
        "    text: Synthetic opportunity\n    domain: learning\n    archetype: opportunity\n"
        "    first_detected: '2026-04-14'\n    last_seen: '2026-04-14'\n"
        "    detection_count: 2\n    materialized: false\n    materialization_threshold: 2\n"
        "    evidence:\n      - '2026-04-14: test — synthetic evidence'\n    candidate_type: scenario\n"
        "    candidate_title: Synthetic opportunity\n---\n# Planning Signals\n",
        encoding="utf-8",
    )


def test_planning_signal_freshness_on_both_surfaces(tmp_path, monkeypatch):
    from datetime import date
    import preflight.state_checks as pkg_checks

    class FrozenDate(date):
        @classmethod
        def today(cls):
            return date(2026, 4, 15)

    cli = _load_cli_preflight(monkeypatch, "_preflight_planning_signals_script")
    monkeypatch.setattr(pkg_checks, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cli, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "SCRIPTS_DIR", str(SCRIPTS))
    for mod in (pkg_checks, cli):
        monkeypatch.setattr(mod, "date", FrozenDate)

    for last_updated, passed in [("2026-04-14", True), ("2026-04-13", False), ("2026-04-16", False), (None, False), ("not-a-date", False)]:
        _write_planning_signals(tmp_path / "planning_signals.md", last_updated)
        for mod in (pkg_checks, cli):
            res = mod.check_planning_signals()
            assert res.severity == "P1" and res.passed is passed

    _write_planning_signals(tmp_path / "planning_signals.md", "2026-04-10")
    p = tmp_path / "planning_signals.md"
    p.write_text(p.read_text(encoding="utf-8").replace("materialized: false", "materialized: true"), encoding="utf-8")
    assert pkg_checks.check_planning_signals().passed and cli.check_planning_signals().passed


def test_briefings_archive_coverage_vscode_and_session_history(tmp_path, monkeypatch):
    import os
    import time
    from datetime import datetime
    import preflight as pf

    today = datetime.now().strftime("%Y-%m-%d")
    for d in ("briefings", "state", "tmp"):
        (tmp_path / d).mkdir()
    monkeypatch.setattr(pf, "ARTHA_DIR", str(tmp_path))

    assert pf.check_briefings_archive_coverage().severity == "P1"
    briefing = tmp_path / "briefings" / f"{today}.md"
    briefing.write_text(f"---\ndate: {today}\nsource: telegram\n---\n\nBriefing.\n", encoding="utf-8")
    assert pf.check_briefings_archive_coverage().passed

    sh = tmp_path / "tmp" / "session_history_1.md"
    sh.write_text("# Session\n", encoding="utf-8")
    old = time.time() - 86400
    os.utime(str(sh), (old, old))
    assert pf.check_briefings_archive_coverage().passed

    os.utime(str(sh), (time.time(), time.time()))
    assert not pf.check_briefings_archive_coverage().passed

    briefing.write_text(f"---\ndate: {today}\nsource: vscode\n---\n\nBriefing.\n", encoding="utf-8")
    assert pf.check_briefings_archive_coverage().passed
