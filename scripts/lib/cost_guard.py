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

import copy
import json
import math
import os
import re
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
_MAX_AMOUNT_USD = 1_000_000_000_000.0
_MAX_RECENT_CALLS = 100_000


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass
class BudgetConfig:
    """Per-agent budget limits."""
    daily_limit_usd: float
    monthly_limit_usd: float

    def __post_init__(self) -> None:
        self.daily_limit_usd = _amount(self.daily_limit_usd, "daily_limit_usd")
        self.monthly_limit_usd = _amount(self.monthly_limit_usd, "monthly_limit_usd")


@dataclass
class _AgentState:
    """Mutable state for one agent."""
    daily_spent: float = 0.0
    monthly_spent: float = 0.0
    last_reset_day: str = ""       # "YYYY-MM-DD"
    last_reset_month: str = ""     # "YYYY-MM"
    # Recent per-call costs for anomaly detection (rolling 7-day window)
    recent_calls: list[dict] = field(default_factory=list)  # [{ts, cost}]
    receipts: dict[str, float] = field(default_factory=dict)


class CostGuardStorageError(RuntimeError):
    """Persisted accounting is unavailable, corrupt or could not be made durable."""


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
        self._state_path = _STATE_FILE
        self._load_state()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_budget(self, agent_id: str, config: BudgetConfig) -> None:
        """Register or update the budget for *agent_id*."""
        if not isinstance(agent_id, str) or not agent_id.strip() or len(agent_id) > 512:
            raise ValueError("agent_id must be nonempty text of at most 512 characters")
        if not isinstance(config, BudgetConfig):
            raise ValueError("config must be a BudgetConfig")
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
        _text(agent_id, "agent_id")
        try:
            estimated_cost = _amount(estimated_cost, "estimated_cost")
        except ValueError:
            return False
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
        _text(agent_id, "agent_id")
        cost_usd = _amount(cost_usd, "cost_usd")
        _text(task_id, "task_id")
        before = copy.deepcopy(self._state)
        self._maybe_rollover(agent_id)
        state = self._get_state(agent_id)
        prior = state.receipts.get(task_id)
        if prior is not None:
            if prior != cost_usd:
                raise ValueError("usage receipt is immutable for this task")
            return False

        state.daily_spent += cost_usd
        state.monthly_spent += cost_usd
        if len(state.receipts) >= _MAX_RECENT_CALLS:
            self._state = before
            raise ValueError("monthly receipt bound is exhausted")
        state.receipts[task_id] = cost_usd

        # Track for anomaly detection
        now_ts = datetime.now(timezone.utc).isoformat()
        state.recent_calls.append({"ts": now_ts, "cost": cost_usd, "task_id": task_id})

        # Prune calls older than _MOVING_AVG_DAYS days
        cutoff = time.time() - (_MOVING_AVG_DAYS * 86400)
        state.recent_calls = [
            c for c in state.recent_calls
            if _iso_to_ts(c.get("ts", "")) >= cutoff
        ]
        state.recent_calls = state.recent_calls[-_MAX_RECENT_CALLS:]

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

        try:
            self._persist_state()
        except CostGuardStorageError:
            self._state = before
            raise
        return is_anomaly

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def agent_summary(self, agent_id: str) -> dict:
        """Return a summary dict for *agent_id* with spending + budget info."""
        _text(agent_id, "agent_id")
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
            state.receipts = {}

    def _load_state(self) -> None:
        """Load persisted state; malformed accounting blocks this guard."""
        if not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"),
                             parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
            if not isinstance(raw, dict):
                raise ValueError("root is not an object")
            loaded: dict[str, _AgentState] = {}
            for agent_id, data in raw.items():
                if (not isinstance(agent_id, str) or not agent_id.strip()
                        or len(agent_id) > 512 or not isinstance(data, dict)):
                    raise ValueError("agent state is malformed")
                recent = data.get("recent_calls", [])
                if not isinstance(recent, list) or len(recent) > _MAX_RECENT_CALLS:
                    raise ValueError("recent calls are malformed")
                calls = []
                for item in recent:
                    if not isinstance(item, dict):
                        raise ValueError("recent call is malformed")
                    _text(item.get("ts"), "recent call timestamp")
                    _text(item.get("task_id"), "receipt task id")
                    try:
                        stamp = datetime.fromisoformat(item["ts"])
                    except ValueError as exc:
                        raise ValueError("recent call timestamp is malformed") from exc
                    if stamp.tzinfo is None:
                        raise ValueError("recent call timestamp lacks a timezone")
                    calls.append({
                        "ts": item["ts"],
                        "cost": _amount(item["cost"], "recent call cost"),
                        "task_id": item["task_id"],
                    })
                receipts_raw = data.get("receipts", {
                    str(item["task_id"]): float(item["cost"]) for item in calls
                })
                if (not isinstance(receipts_raw, dict)
                        or len(receipts_raw) > _MAX_RECENT_CALLS):
                    raise ValueError("receipts are malformed")
                receipts = {}
                for task_id, cost in receipts_raw.items():
                    _text(task_id, "receipt task id")
                    receipts[task_id] = _amount(cost, "receipt cost")
                last_day = data.get("last_reset_day")
                last_month = data.get("last_reset_month")
                if (not isinstance(last_day, str)
                        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", last_day)
                        or not isinstance(last_month, str)
                        or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", last_month)):
                    raise ValueError("reset dates are malformed")
                try:
                    datetime.strptime(last_day, "%Y-%m-%d")
                    datetime.strptime(last_month, "%Y-%m")
                except ValueError as exc:
                    raise ValueError("reset dates are malformed") from exc
                monthly_spent = _amount(data.get("monthly_spent", 0.0), "monthly_spent")
                if "receipts" in data and abs(sum(receipts.values()) - monthly_spent) > 1e-9:
                    raise ValueError("receipt total differs from monthly accounting")
                loaded[agent_id] = _AgentState(
                    daily_spent=_amount(data.get("daily_spent", 0.0), "daily_spent"),
                    monthly_spent=monthly_spent,
                    last_reset_day=last_day,
                    last_reset_month=last_month,
                    recent_calls=calls,
                    receipts=receipts,
                )
            self._state = loaded
        except (OSError, UnicodeError, TypeError, ValueError, KeyError) as exc:
            raise CostGuardStorageError("cost accounting is unavailable or corrupt") from exc

    def _persist_state(self) -> None:
        """Atomically flush in-memory accounting; a failed write is not success."""
        temporary = self._state_path.with_name(f".{self._state_path.name}.tmp")
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            serialisable = {
                agent_id: {
                    "daily_spent": s.daily_spent,
                    "monthly_spent": s.monthly_spent,
                    "last_reset_day": s.last_reset_day,
                    "last_reset_month": s.last_reset_month,
                    "recent_calls": s.recent_calls,
                    "receipts": s.receipts,
                }
                for agent_id, s in self._state.items()
            }
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(serialisable, handle, indent=2, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._state_path)
            if os.name != "nt":
                directory = os.open(self._state_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except (OSError, TypeError, ValueError) as exc:
            temporary.unlink(missing_ok=True)
            raise CostGuardStorageError("cost accounting could not be persisted") from exc


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------

def _iso_to_ts(iso: str) -> float:
    """Convert ISO-8601 UTC string to Unix timestamp. Returns 0.0 on error."""
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 0.0


def _amount(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite nonnegative amount")
    amount = float(value)
    if not math.isfinite(amount) or not 0 <= amount <= _MAX_AMOUNT_USD:
        raise ValueError(f"{name} must be a finite nonnegative amount")
    return amount


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f"{name} must be nonempty text of at most 512 characters")
    return value
