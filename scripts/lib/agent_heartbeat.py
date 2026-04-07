# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/agent_heartbeat.py — Heartbeat health monitor for agent fleet (EAR-8).

Runs as part of pipeline.py Step 0 (preflight) and `work brief` invocation.
Performs lightweight file-stat checks (<10ms total) — no network, no LLM calls.

Checks:
  1. Stale cache: knowledge cache file age > cache_ttl_days
  2. Declining quality: mean_quality_score declining over recent invocations
  3. Idle agents: scheduled but no invocations in 7 days
  4. Consecutive failures: health.consecutive_failures ≥ threshold
  5. Approaching retirement: consecutive_failures approaching suspension limit

Alerts surface in briefing under § Agent Fleet Health (zero-noise when healthy).

Alert format:
  ⚠️  <agent-name>: <reason> — run: <suggested command>
  🔴 <agent-name>: <critical reason> — <action>

Ref: specs/ext-agent-reloaded.md §EAR-8
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_CACHE_STALE_DAYS_DEFAULT = 7
_CONSECUTIVE_FAIL_WARN = 3
_CONSECUTIVE_FAIL_CRITICAL = 5
_QUALITY_WARN_THRESHOLD = 0.5
_IDLE_DAYS_WARN = 7
_CACHE_DIR = (
    Path(__file__).resolve().parent.parent.parent / "tmp" / "ext-agent-cache"
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class HealthAlert:
    agent_name: str
    severity: str          # "warn" | "critical"
    reason: str
    suggested_command: str = ""

    @property
    def icon(self) -> str:
        return "🔴" if self.severity == "critical" else "⚠️"

    def format_line(self) -> str:
        cmd = f" — run: {self.suggested_command}" if self.suggested_command else ""
        return f"{self.icon} {self.agent_name}: {self.reason}{cmd}"


# ---------------------------------------------------------------------------
# AgentHeartbeat
# ---------------------------------------------------------------------------

class AgentHeartbeat:
    """Checks agent fleet health. All checks are file-stat operations.

    Parameters:
        registry: AgentRegistry instance.
        cache_dir: Knowledge cache directory override.
        shard_dir: Health shard directory override.
    """

    def __init__(
        self,
        registry,
        cache_dir: Path | None = None,
        shard_dir: Path | None = None,
    ) -> None:
        self._registry = registry
        self._cache_dir = cache_dir or _CACHE_DIR
        self._shard_dir = shard_dir

    def check(self) -> list[HealthAlert]:
        """Run all heartbeat checks. Returns list of alerts (empty = healthy).

        Never raises — individual check failures are silently skipped.
        """
        alerts: list[HealthAlert] = []

        try:
            agents = list(self._registry.active_agents())
        except Exception:
            return alerts

        for agent in agents:
            try:
                alerts.extend(self._check_agent(agent))
            except Exception:
                continue

        return alerts

    def _check_agent(self, agent) -> list[HealthAlert]:
        """Run all checks for a single agent."""
        alerts: list[HealthAlert] = []
        name = agent.name

        # Check 1: Stale cache
        cache_file = self._cache_dir / f"{name}.md"
        if cache_file.exists():
            try:
                age_days = (time.time() - cache_file.stat().st_mtime) / 86400
                ttl = getattr(agent, "cache_ttl_days", _CACHE_STALE_DAYS_DEFAULT)
                if age_days > ttl:
                    alerts.append(HealthAlert(
                        agent_name=name,
                        severity="warn",
                        reason=f"Cache {int(age_days)}d old (TTL: {ttl}d)",
                        suggested_command=f"agent_manager refresh-cache {name}",
                    ))
            except OSError:
                pass

        # Check 2: Consecutive failures
        health = getattr(agent, "health", None)
        if health is not None:
            consec = getattr(health, "consecutive_failures", 0) or 0
            if consec >= _CONSECUTIVE_FAIL_CRITICAL:
                alerts.append(HealthAlert(
                    agent_name=name,
                    severity="critical",
                    reason=f"{consec} consecutive failures — approaching suspension",
                    suggested_command=f"agent_manager health {name}",
                ))
            elif consec >= _CONSECUTIVE_FAIL_WARN:
                alerts.append(HealthAlert(
                    agent_name=name,
                    severity="warn",
                    reason=f"{consec} consecutive failures",
                    suggested_command=f"agent_manager health {name}",
                ))

            # Check 3: Low mean quality
            mean_q = getattr(health, "mean_quality_score", 0.0) or 0.0
            total = getattr(health, "total_invocations", 0) or 0
            if total >= 5 and mean_q < _QUALITY_WARN_THRESHOLD:
                alerts.append(HealthAlert(
                    agent_name=name,
                    severity="warn",
                    reason=f"Low mean quality {mean_q:.2f} (≥5 invocations)",
                ))

            # Check 4: Health shard — also check for idle agents
            try:
                from lib.health_shard import HealthShard  # noqa: PLC0415
                shard = HealthShard(shard_dir=self._shard_dir)
                summary = shard.aggregate(name)
                if summary.total_invocations > 0 and summary.last_invocation:
                    try:
                        last_ts = datetime.fromisoformat(
                            summary.last_invocation.replace("Z", "+00:00")
                        )
                        idle_days = (
                            datetime.now(timezone.utc) - last_ts
                        ).total_seconds() / 86400
                        if idle_days > _IDLE_DAYS_WARN:
                            alerts.append(HealthAlert(
                                agent_name=name,
                                severity="warn",
                                reason=f"No invocations in {int(idle_days)} days",
                            ))
                    except (ValueError, TypeError):
                        pass
            except (ImportError, Exception):
                pass

        return alerts

    def format_briefing_section(self, alerts: list[HealthAlert]) -> str:
        """Format alerts as a briefing section. Returns empty string when healthy."""
        if not alerts:
            return ""
        lines = ["§ Agent Fleet Health"]
        for a in alerts:
            lines.append(a.format_line())
        return "\n".join(lines) + "\n"
