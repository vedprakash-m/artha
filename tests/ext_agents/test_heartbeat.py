"""
tests/ext_agents/test_heartbeat.py — EAR-8: heartbeat health monitor tests.

Tests (15):
 1. check() returns list of HealthAlert
 2. healthy fleet → empty alerts
 3. 3 consecutive failures → warn alert
 4. 5 consecutive failures → critical alert
 5. mean quality < 0.5 with ≥5 invocations → quality alert
 6. zero invocations → no quality alert
 7. format_briefing_section returns empty string when no alerts
 8. format_briefing_section returns non-empty when alerts exist
 9. alert has severity field ("warn" or "critical")
10. alert has agent_name field
11. alert has suggested_command field
12. alert format_line contains agent name
13. multiple alerts can fire for same agent
14. check() with no active agents → empty list
15. HealthAlert icon reflects severity

Ref: specs/ext-agent-reloaded.md §EAR-8
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.agent_heartbeat import AgentHeartbeat, HealthAlert


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(
    name="test-agent",
    consecutive_failures=0,
    mean_quality_score=0.9,
    total_invocations=10,
    cache_ttl_days=7,
):
    """Build a mock ExternalAgent with enough attributes for _check_agent."""
    health = MagicMock()
    health.consecutive_failures = consecutive_failures
    health.mean_quality_score = mean_quality_score
    health.total_invocations = total_invocations

    agent = MagicMock()
    agent.name = name
    agent.health = health
    agent.cache_ttl_days = cache_ttl_days
    return agent


def _make_heartbeat(agents: list, tmp_path: Path) -> AgentHeartbeat:
    """Create AgentHeartbeat with a mock registry returning the given agents."""
    registry = MagicMock()
    registry.active_agents.return_value = agents
    return AgentHeartbeat(registry=registry, cache_dir=tmp_path / "cache", shard_dir=tmp_path / "shards")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_check_returns_list(tmp_path):
    hb = _make_heartbeat([], tmp_path)
    result = hb.check()
    assert isinstance(result, list)


def test_healthy_fleet_no_alerts(tmp_path):
    agents = [_make_agent("agent-a"), _make_agent("agent-b")]
    hb = _make_heartbeat(agents, tmp_path)
    alerts = hb.check()
    assert alerts == [], f"Expected no alerts but got: {alerts}"


def test_three_consecutive_failures_warn(tmp_path):
    agent = _make_agent(consecutive_failures=3)
    hb = _make_heartbeat([agent], tmp_path)
    alerts = hb.check()
    fail_alerts = [a for a in alerts if a.severity == "warn"]
    assert fail_alerts, "Expected warn alert for 3 consecutive failures"


def test_five_consecutive_failures_critical(tmp_path):
    agent = _make_agent(consecutive_failures=5)
    hb = _make_heartbeat([agent], tmp_path)
    alerts = hb.check()
    assert any(a.severity == "critical" for a in alerts), \
        f"Expected critical severity for 5 failures; got: {[a.severity for a in alerts]}"


def test_low_mean_quality_alert(tmp_path):
    agent = _make_agent(mean_quality_score=0.3, total_invocations=10)
    hb = _make_heartbeat([agent], tmp_path)
    alerts = hb.check()
    assert any("quality" in a.reason.lower() for a in alerts), \
        f"Expected quality alert; got reasons: {[a.reason for a in alerts]}"


def test_zero_invocations_no_quality_alert(tmp_path):
    """Quality check requires >= 5 invocations to fire."""
    agent = _make_agent(mean_quality_score=0.1, total_invocations=0)
    hb = _make_heartbeat([agent], tmp_path)
    alerts = hb.check()
    quality_alerts = [a for a in alerts if "quality" in a.reason.lower()]
    assert not quality_alerts, "Should not fire quality alert with 0 invocations"


def test_format_briefing_section_empty_when_no_alerts(tmp_path):
    hb = _make_heartbeat([], tmp_path)
    section = hb.format_briefing_section([])
    assert section == "", f"Expected empty string; got: {repr(section)}"


def test_format_briefing_section_non_empty_with_alerts(tmp_path):
    hb = _make_heartbeat([], tmp_path)
    alert = HealthAlert(
        agent_name="foo",
        severity="critical",
        reason="5 consecutive failures",
        suggested_command="agent_manager health foo",
    )
    section = hb.format_briefing_section([alert])
    assert len(section) > 0, "Expected non-empty briefing section"


def test_alert_has_severity(tmp_path):
    agent = _make_agent(consecutive_failures=3)
    hb = _make_heartbeat([agent], tmp_path)
    alerts = hb.check()
    for a in alerts:
        assert a.severity in ("warn", "critical"), f"Invalid severity: {a.severity!r}"


def test_alert_has_agent_name(tmp_path):
    agent = _make_agent(name="my-special-agent", consecutive_failures=5)
    hb = _make_heartbeat([agent], tmp_path)
    alerts = hb.check()
    for a in alerts:
        assert hasattr(a, "agent_name"), "HealthAlert missing agent_name"
        assert a.agent_name == "my-special-agent"


def test_alert_has_suggested_command(tmp_path):
    agent = _make_agent(consecutive_failures=5)
    hb = _make_heartbeat([agent], tmp_path)
    alerts = hb.check()
    for a in alerts:
        assert hasattr(a, "suggested_command")
        assert isinstance(a.suggested_command, str)


def test_alert_format_line_contains_name(tmp_path):
    alert = HealthAlert(
        agent_name="fleet-agent",
        severity="warn",
        reason="3 consecutive failures",
    )
    line = alert.format_line()
    assert "fleet-agent" in line


def test_multiple_alerts_same_agent(tmp_path):
    agent = _make_agent(
        consecutive_failures=5,
        mean_quality_score=0.2,
        total_invocations=20,
    )
    hb = _make_heartbeat([agent], tmp_path)
    alerts = hb.check()
    # At least critical failure + quality alert
    assert len(alerts) >= 2, f"Expected ≥2 alerts; got: {[a.reason for a in alerts]}"


def test_check_no_agents_empty(tmp_path):
    hb = _make_heartbeat([], tmp_path)
    alerts = hb.check()
    assert alerts == []


def test_healthalert_icon_reflects_severity():
    warn = HealthAlert(agent_name="x", severity="warn", reason="test")
    crit = HealthAlert(agent_name="x", severity="critical", reason="test")
    assert warn.icon == "⚠️"
    assert crit.icon == "🔴"
