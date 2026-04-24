"""
cost_guard.py — ST-03 Budget enforcement + anomaly detection for AI agents.
specs/steal.md §15.4.3

Tracks per-agent daily and monthly spending, blocks calls that would exceed
configured limits, and flags anomalous spikes (>3× the agent's 7-day moving
average cost per call).

State is persisted to ~/.artha-local/cost_guard_state.json (per OQ-5:
NOT in OneDrive to avoid sync contention).

Design constraints:
  - R8: dataclasses only, no Pydantic
  - Thread-safety: not required (single-process, sequential)
  - Budget config loaded from config/artha_config.yaml cost_budgets section
    OR set programmatically via set_budget()
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Where state lives (OQ-5: NOT in OneDrive)
# ---------------------------------------------------------------------------

_STATE_DIR = Path.home() / ".artha-local"
_STATE_FILE = _STATE_DIR / "cost_guard_state.json"

# Anomaly detection multiplier
_SPIKE_MULTIPLIER = 3.0

# Moving-average window (days)
_MOVING_AVG_DAYS = 7


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass
class BudgetConfig:
    """Per-agent budget limits."""
    daily_limit_usd: float
    monthly_limit_usd: float


@dataclass
class _AgentState:
    """Mutable state for one agent."""
    daily_spent: float = 0.0
    monthly_spent: float = 0.0
    last_reset_day: str = ""       # "YYYY-MM-DD"
    last_reset_month: str = ""     # "YYYY-MM"
    # Recent per-call costs for anomaly detection (rolling 7-day window)
    recent_calls: list[dict] = field(default_factory=list)  # [{ts, cost}]


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class CostGuard:
    """Budget enforcement and anomaly detection for AI agent calls.

    Usage::

        guard = CostGuard()
        guard.set_budget("pipeline_llm", BudgetConfig(1.00, 20.00))

        if guard.check("pipeline_llm", estimated_cost=0.05):
            # proceed
            guard.record("pipeline_llm", task_id="run-001", cost_usd=0.048)
        else:
            raise RuntimeError("Budget exceeded")
    """

    def __init__(self) -> None:
        self._budgets: dict[str, BudgetConfig] = {}
        self._state: dict[str, _AgentState] = {}
        self._load_state()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_budget(self, agent_id: str, config: BudgetConfig) -> None:
        """Register or update the budget for *agent_id*."""
        self._budgets[agent_id] = config

    # ------------------------------------------------------------------
    # Budget check
    # ------------------------------------------------------------------

    def check(self, agent_id: str, estimated_cost: float) -> bool:
        """Return True if *estimated_cost* fits within *agent_id*'s budget.

        Returns False (block) when:
          - daily_spent + estimated_cost would exceed daily_limit_usd, OR
          - monthly_spent + estimated_cost would exceed monthly_limit_usd

        If no budget is configured for *agent_id*, returns True (allow-all).
        """
        self._maybe_rollover(agent_id)

        budget = self._budgets.get(agent_id)
        if budget is None:
            return True  # No budget configured → allow

        state = self._get_state(agent_id)

        if state.daily_spent + estimated_cost > budget.daily_limit_usd:
            return False
        if state.monthly_spent + estimated_cost > budget.monthly_limit_usd:
            return False
        return True

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        agent_id: str,
        task_id: str,
        cost_usd: float,
        *,
        anomaly_callback: Any = None,
    ) -> bool:
        """Record a completed call's actual cost.

        Args:
            agent_id:         Agent identifier.
            task_id:          Opaque task/run identifier (for audit trail).
            cost_usd:         Actual cost of this call.
            anomaly_callback: Optional callable(agent_id, cost_usd, avg_cost)
                              called when a spike is detected.

        Returns:
            True if the cost was an anomaly (spike detected), False otherwise.
        """
        self._maybe_rollover(agent_id)
        state = self._get_state(agent_id)

        state.daily_spent += cost_usd
        state.monthly_spent += cost_usd

        # Track for anomaly detection
        now_ts = datetime.now(timezone.utc).isoformat()
        state.recent_calls.append({"ts": now_ts, "cost": cost_usd, "task_id": task_id})

        # Prune calls older than _MOVING_AVG_DAYS days
        cutoff = time.time() - (_MOVING_AVG_DAYS * 86400)
        state.recent_calls = [
            c for c in state.recent_calls
            if _iso_to_ts(c.get("ts", "")) >= cutoff
        ]

        # Anomaly detection
        is_anomaly = False
        if len(state.recent_calls) > 1:
            prior_costs = [c["cost"] for c in state.recent_calls[:-1]]
            avg = sum(prior_costs) / len(prior_costs)
            if avg > 0 and cost_usd > _SPIKE_MULTIPLIER * avg:
                is_anomaly = True
                if callable(anomaly_callback):
                    try:
                        anomaly_callback(agent_id, cost_usd, avg)
                    except Exception:
                        pass

        self._persist_state()
        return is_anomaly

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def agent_summary(self, agent_id: str) -> dict:
        """Return a summary dict for *agent_id* with spending + budget info."""
        self._maybe_rollover(agent_id)
        state = self._get_state(agent_id)
        budget = self._budgets.get(agent_id)

        recent = [c for c in state.recent_calls]
        avg_cost = (
            sum(c["cost"] for c in recent) / len(recent) if recent else 0.0
        )

        return {
            "agent_id": agent_id,
            "daily_spent_usd": round(state.daily_spent, 6),
            "monthly_spent_usd": round(state.monthly_spent, 6),
            "daily_limit_usd": budget.daily_limit_usd if budget else None,
            "monthly_limit_usd": budget.monthly_limit_usd if budget else None,
            "avg_cost_per_call_7d": round(avg_cost, 6),
            "recent_call_count": len(recent),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_state(self, agent_id: str) -> _AgentState:
        if agent_id not in self._state:
            self._state[agent_id] = _AgentState()
        return self._state[agent_id]

    def _maybe_rollover(self, agent_id: str) -> None:
        """Reset daily/monthly counters if the calendar day/month has rolled over."""
        state = self._get_state(agent_id)
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        this_month = now.strftime("%Y-%m")

        if state.last_reset_day != today:
            state.daily_spent = 0.0
            state.last_reset_day = today

        if state.last_reset_month != this_month:
            state.monthly_spent = 0.0
            state.last_reset_month = this_month

    def _load_state(self) -> None:
        """Load persisted state from ~/.artha-local/cost_guard_state.json."""
        if not _STATE_FILE.exists():
            return
        try:
            raw: dict = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            for agent_id, data in raw.items():
                s = _AgentState()
                s.daily_spent = float(data.get("daily_spent", 0.0))
                s.monthly_spent = float(data.get("monthly_spent", 0.0))
                s.last_reset_day = str(data.get("last_reset_day", ""))
                s.last_reset_month = str(data.get("last_reset_month", ""))
                s.recent_calls = list(data.get("recent_calls", []))
                self._state[agent_id] = s
        except Exception:
            # Corrupted state → start fresh (non-critical)
            self._state = {}

    def _persist_state(self) -> None:
        """Flush in-memory state to ~/.artha-local/cost_guard_state.json."""
        try:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            serialisable = {
                agent_id: {
                    "daily_spent": s.daily_spent,
                    "monthly_spent": s.monthly_spent,
                    "last_reset_day": s.last_reset_day,
                    "last_reset_month": s.last_reset_month,
                    "recent_calls": s.recent_calls,
                }
                for agent_id, s in self._state.items()
            }
            _STATE_FILE.write_text(
                json.dumps(serialisable, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass  # Best-effort persistence


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------

def _iso_to_ts(iso: str) -> float:
    """Convert ISO-8601 UTC string to Unix timestamp. Returns 0.0 on error."""
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 0.0
