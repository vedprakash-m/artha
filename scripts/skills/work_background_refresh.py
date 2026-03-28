"""
scripts/skills/work_background_refresh.py — Pull-triggered work connector refresh skill.

Implements §8.7 (Pull-Triggered Pre-computation Model) and §22 (Work Skill Registry).
Registered in config/skills.yaml as work_background_refresh (priority: P0).

This skill runs the full Work Operating Loop in REFRESH mode. It is invoked in
two ways — both user-pull-triggered, never autonomously (§3.9):
  1. As a post-commit stage of /catch-up (when work.refresh.run_on_catchup: true)
     via scripts/post_work_refresh.py — fires after the personal briefing is delivered.
  2. On-demand via /work refresh (explicit user pull between catch-ups).

No cron job, no Task Scheduler, no LaunchAgent. The user's daily /catch-up is
the scheduler. This is consistent with the PRD §8 pull-model architecture and
the v3.0 architectural pivot away from daemon infrastructure.

Runtime selection (§21.1): The WorkLoop automatically checks for Agency
first on Windows and delegates to the appropriate Agency agent tier.
When Agency is unavailable, it falls back to direct pipeline.py connectors.
This skill does not need to handle runtime selection — WorkLoop does.

It follows the same BaseSkill pattern as all other Artha skills.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from skills.base_skill import BaseSkill  # type: ignore

log = logging.getLogger("artha.skills.work_background_refresh")


class WorkBackgroundRefreshSkill(BaseSkill):
    """
    Pull-triggered work connector refresh skill (§8.7).

    Runs the full WorkLoop (REFRESH mode) to update all state/work/*.md
    files atomically. This is the P0 work skill — all read-path commands
    depend on it.

    Invocation paths (both user-pull-triggered, never autonomous per §3.9):
      1. /catch-up post-commit stage via post_work_refresh.py
      2. /work refresh explicit on-demand pull

    Platform: all (workiq/outlook are platform-gated inside WorkLoop).
    Requires no OS scheduler — works identically on macOS, Windows, Linux.
    """

    def __init__(self) -> None:
        super().__init__(name="work_background_refresh", priority="P0")

    def pull(self) -> Dict[str, Any]:
        """
        Execute the Work Operating Loop in REFRESH mode.
        Returns the LoopResult serialised as a dict.
        """
        from work_loop import WorkLoop, LoopMode  # type: ignore

        log.info("work_background_refresh: starting REFRESH loop")
        loop = WorkLoop(mode=LoopMode.REFRESH)
        result = loop.run()
        log.info(
            "work_background_refresh: REFRESH complete — providers=%s degraded=%s errors=%d",
            result.providers.names,
            result.degraded_providers,
            len(result.errors),
        )
        return {
            "run_id": result.run_id,
            "mode": result.mode.value,
            "providers": result.providers.names,
            "degraded": result.degraded_providers,
            "errors": result.errors,
            "stages": result.stages_completed,
            "is_stale": result.is_stale,
            "freshness_footer": result.freshness_footer,
        }

    def parse(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Pass through — pull() already returns structured data."""
        return raw_data

    def to_dict(self) -> Dict[str, Any]:
        """Return skill metadata for the skill runner."""
        return {
            "name": self.name,
            "priority": self.priority,
            "status": self.status,
            "last_run": self.last_run,
            "error": self.error,
        }

    @property
    def compare_fields(self) -> List[str]:
        """Fields used to detect changes between skill runs."""
        return ["providers", "degraded", "errors"]
