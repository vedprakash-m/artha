"""WP-02 / RA-17: stale-on-open banner (pull-only staleness honesty)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import stale_banner as sb  # noqa: E402

NOW = "2026-10-11T20:00:00Z"


def test_fresh_catchup_has_single_line_no_warning():
    out = sb.build_banner(now=NOW, last_catch_up="2026-10-11T15:50:00Z", entries={})
    assert out.count("\n") == 0
    assert "4h ago" in out and "⚠" not in out


def test_old_catchup_warns_past_threshold():
    out = sb.build_banner(now=NOW, last_catch_up="2026-10-05T15:50:00Z", entries={})
    assert "⚠" in out and "6 days" in out


def test_exactly_at_threshold_is_not_a_warning():
    out = sb.build_banner(now="2026-10-13T15:50:00Z", last_catch_up="2026-10-11T15:50:00Z", entries={})
    assert "⚠" not in out


def test_missing_catchup_is_stated_not_hidden():
    out = sb.build_banner(now=NOW, last_catch_up=None, entries={})
    assert "no completed Catch-up" in out and "⚠" in out


def test_stale_source_listed_with_age():
    entries = {"google_calendar": {"last_success_at": "2026-08-30T00:00:00Z",
                                   "failing_since": "2026-08-31T00:00:00Z"},
               "gmail": {"last_success_at": "2026-10-11T15:50:00Z"}}
    out = sb.build_banner(now=NOW, last_catch_up="2026-10-11T15:50:00Z", entries=entries)
    assert "google_calendar" in out and "42 days" in out
    assert "gmail" not in out


def test_source_never_succeeded_is_listed():
    out = sb.build_banner(now=NOW, last_catch_up="2026-10-11T15:50:00Z",
                          entries={"icloud_email": {"failing_since": "2026-10-01T00:00:00Z"}})
    assert "icloud_email" in out and "never" in out


def test_read_last_catchup_from_health_check(tmp_path):
    hc = tmp_path / "health-check.md"
    hc.write_text('---\nschema_version: \'1.1\'\nlast_catch_up: "2026-10-11T15:50:00Z"\ncatch_up_count: 79\n---\nbody\n')
    assert sb.read_last_catch_up(hc) == "2026-10-11T15:50:00Z"


def test_read_last_catchup_missing_or_malformed(tmp_path):
    assert sb.read_last_catch_up(tmp_path / "nope.md") is None
    bad = tmp_path / "bad.md"
    bad.write_text("no frontmatter at all")
    assert sb.read_last_catch_up(bad) is None


def test_main_prints_banner_and_exits_zero(tmp_path, capsys):
    hc = tmp_path / "health-check.md"
    hc.write_text('---\nlast_catch_up: "2026-10-01T00:00:00Z"\n---\n')
    cov = tmp_path / "cov.json"
    cov.write_text('{"connectors": {}}')
    rc = sb.main(["--health-check", str(hc), "--coverage", str(cov), "--now", NOW])
    assert rc == 0
    assert "Last Catch-up" in capsys.readouterr().out


def test_core_catch_up_counts_when_authoritative_and_is_newer(tmp_path):
    import json
    import stale_banner as sb
    core = tmp_path / "health.json"
    core.write_text(json.dumps({"last_catch_up": "2026-10-20T10:00:00+00:00", "mode": "authoritative"}))
    assert sb.read_core_catch_up(core) == "2026-10-20T10:00:00+00:00"
    assert sb.latest("2026-10-11T15:50:00Z", sb.read_core_catch_up(core)) == "2026-10-20T10:00:00+00:00"


def test_shadow_or_unreadable_core_health_never_renews_freshness(tmp_path):
    import json
    import stale_banner as sb
    core = tmp_path / "health.json"
    core.write_text(json.dumps({"last_catch_up": "2026-10-20T10:00:00+00:00", "mode": "shadow"}))
    assert sb.read_core_catch_up(core) is None
    core.write_text("{not json")
    assert sb.read_core_catch_up(core) is None
    assert sb.read_core_catch_up(tmp_path / "missing.json") is None
    assert sb.latest(None, None) is None
    assert sb.latest("garbage", None) == "garbage"
