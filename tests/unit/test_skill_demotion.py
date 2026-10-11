"""WP-03 / §6.7: a skill that keeps failing cannot stay 'trusted' (default: 5 consecutive failures)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import skill_health as sh  # noqa: E402

OK = {"current": {"status": "success", "data": {}}}
FAIL = {"current": {"status": "failed", "error": "boom"}}


def _run(entry, template):
    e = {**entry, **template}
    return sh.update_health_counters(e, is_zero=False, is_stable=False)


def _seed(successes=20):
    entry = {}
    for _ in range(successes):
        entry = _run(entry, OK)
    return entry


def test_trusted_skill_demoted_after_five_consecutive_failures():
    entry = _seed(20)
    assert entry["health"]["maturity"] == "trusted"
    for i in range(4):
        entry = _run(entry, FAIL)
        assert entry["health"]["maturity"] == "trusted", f"not yet demoted after {i + 1} failures"
    entry = _run(entry, FAIL)
    assert entry["health"]["consecutive_failures"] == 5
    assert entry["health"]["maturity"] == "broken"
    assert entry["health"]["classification"] == "broken"


def test_one_success_recovers_and_resets_counter():
    entry = _seed(20)
    for _ in range(7):
        entry = _run(entry, FAIL)
    assert entry["health"]["maturity"] == "broken"
    entry = _run(entry, OK)
    assert entry["health"]["consecutive_failures"] == 0
    assert entry["health"]["maturity"] == "trusted"
    assert entry["health"]["classification"] != "broken"


def test_label_and_classification_never_disagree():
    """Observed: maturity 'trusted' with classification 'broken' and 206 failures."""
    entry = _seed(2)
    for _ in range(30):
        entry = _run(entry, FAIL)
        h = entry["health"]
        assert not (h["maturity"] == "trusted" and h["classification"] == "broken")


def test_threshold_is_configurable():
    entry = _seed(20)
    for _ in range(3):
        entry = sh.update_health_counters({**entry, **FAIL}, False, False, demotion_threshold=3)
    assert entry["health"]["maturity"] == "broken"


def test_legacy_entry_without_counter_is_handled():
    legacy = {"health": {"total_runs": 40, "success_count": 0, "failure_count": 40,
                         "maturity": "trusted", "classification": "broken"}, **FAIL}
    out = sh.update_health_counters(legacy, False, False)
    assert out["health"]["maturity"] == "broken"
    assert out["health"]["consecutive_failures"] >= 1


# ── runner behaviour ─────────────────────────────────────────────────────────

def test_broken_skill_is_probed_weekly_not_every_run():
    import skill_runner as sr  # noqa: PLC0415
    now = datetime.now(timezone.utc)
    cfg = {"skills": {"watch_monitor": {"enabled": True, "cadence": "every_run", "priority": "P2"}}}
    cache = {"watch_monitor": {"last_run": (now - timedelta(days=1)).isoformat(),
                               "health": {"maturity": "broken", "consecutive_failures": 9}}}
    assert sr.should_run("watch_monitor", cfg, cache) is False
    cache["watch_monitor"]["last_run"] = (now - timedelta(days=8)).isoformat()
    assert sr.should_run("watch_monitor", cfg, cache) is True


def test_p0_skill_is_never_suppressed_by_demotion():
    import skill_runner as sr  # noqa: PLC0415
    now = datetime.now(timezone.utc)
    cfg = {"skills": {"uscis_status": {"enabled": True, "cadence": "every_run", "priority": "P0"}}}
    cache = {"uscis_status": {"last_run": (now - timedelta(hours=1)).isoformat(),
                              "health": {"maturity": "broken", "consecutive_failures": 9}}}
    assert sr.should_run("uscis_status", cfg, cache) is True
