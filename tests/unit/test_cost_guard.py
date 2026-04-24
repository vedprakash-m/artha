"""tests/unit/test_cost_guard.py — Tests for cost_guard.py. specs/steal.md §15.4.3"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"))

from cost_guard import BudgetConfig, CostGuard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_guard(tmp_path: Path) -> CostGuard:
    """Return a CostGuard that persists state to tmp_path, not ~/.artha-local."""
    guard = CostGuard()
    # Override the state path so tests never touch the real ~/.artha-local/
    guard._state_path = tmp_path / "cost_guard_state.json"  # monkey-patch for isolation
    # Patch _persist_state to write to our tmp file
    real_persist = guard._persist_state.__func__

    def _patched_persist():
        import json as _json
        guard._state_path.parent.mkdir(parents=True, exist_ok=True)
        serialisable = {
            agent_id: {
                "daily_spent": s.daily_spent,
                "monthly_spent": s.monthly_spent,
                "last_reset_day": s.last_reset_day,
                "last_reset_month": s.last_reset_month,
                "recent_calls": s.recent_calls,
            }
            for agent_id, s in guard._state.items()
        }
        guard._state_path.write_text(
            _json.dumps(serialisable, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    import types
    guard._persist_state = types.MethodType(lambda self: _patched_persist(), guard)
    return guard


# ---------------------------------------------------------------------------
# Budget allow/block
# ---------------------------------------------------------------------------

class TestBudgetCheckAllowsAndBlocks:
    def test_check_allows_within_budget(self, tmp_path):
        guard = CostGuard()
        guard._state = {}  # fresh state
        guard.set_budget("agent1", BudgetConfig(daily_limit_usd=1.00, monthly_limit_usd=10.00))
        assert guard.check("agent1", 0.50) is True

    def test_check_blocks_over_daily_budget(self, tmp_path):
        guard = CostGuard()
        guard._state = {}
        guard.set_budget("agent1", BudgetConfig(daily_limit_usd=1.00, monthly_limit_usd=10.00))
        # Record 0.80 to get close to limit
        guard.record("agent1", "task-1", 0.80)
        # Now 0.30 more would exceed the 1.00 limit
        assert guard.check("agent1", 0.30) is False

    def test_check_blocks_over_monthly_budget(self, tmp_path):
        guard = CostGuard()
        guard._state = {}
        guard.set_budget("agent1", BudgetConfig(daily_limit_usd=100.00, monthly_limit_usd=1.00))
        guard.record("agent1", "task-1", 0.80)
        assert guard.check("agent1", 0.30) is False

    def test_check_allows_with_no_budget_configured(self, tmp_path):
        guard = CostGuard()
        guard._state = {}
        # No budget set for "unknown_agent"
        assert guard.check("unknown_agent", 99.99) is True


# ---------------------------------------------------------------------------
# Record updates state
# ---------------------------------------------------------------------------

class TestRecordUpdatesDailySpent:
    def test_record_updates_daily_spent(self, tmp_path):
        guard = CostGuard()
        guard._state = {}
        guard.set_budget("agent1", BudgetConfig(daily_limit_usd=10.0, monthly_limit_usd=100.0))
        guard.record("agent1", "task-1", 0.25)
        guard.record("agent1", "task-2", 0.10)
        summary = guard.agent_summary("agent1")
        assert abs(summary["daily_spent_usd"] - 0.35) < 1e-9

    def test_record_updates_monthly_spent(self, tmp_path):
        guard = CostGuard()
        guard._state = {}
        guard.set_budget("agent1", BudgetConfig(daily_limit_usd=10.0, monthly_limit_usd=100.0))
        guard.record("agent1", "task-1", 1.50)
        summary = guard.agent_summary("agent1")
        assert abs(summary["monthly_spent_usd"] - 1.50) < 1e-9


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------

class TestAnomalyDetection:
    def test_anomaly_detection_flags_spike(self, tmp_path):
        guard = CostGuard()
        guard._state = {}
        guard.set_budget("agent1", BudgetConfig(daily_limit_usd=1000.0, monthly_limit_usd=10000.0))

        # Build a moving average of ~0.10 per call
        for i in range(6):
            guard.record("agent1", f"task-baseline-{i}", 0.10)

        # Now spike at 0.50 → >3× avg (0.10)
        spike_calls = []
        guard.record("agent1", "task-spike", 0.50, anomaly_callback=lambda *args: spike_calls.append(args))
        assert len(spike_calls) == 1

    def test_no_anomaly_on_normal_cost(self, tmp_path):
        guard = CostGuard()
        guard._state = {}
        guard.set_budget("agent1", BudgetConfig(daily_limit_usd=1000.0, monthly_limit_usd=10000.0))

        for i in range(6):
            guard.record("agent1", f"task-{i}", 0.10)

        # 0.20 = 2× avg, below the 3× threshold
        spike_calls = []
        is_anomaly = guard.record("agent1", "normal", 0.20, anomaly_callback=lambda *a: spike_calls.append(a))
        assert is_anomaly is False
        assert len(spike_calls) == 0


# ---------------------------------------------------------------------------
# Monthly rollover
# ---------------------------------------------------------------------------

class TestMonthlyRollover:
    def test_daily_rollover_resets_daily_spent(self, tmp_path):
        """Simulate day rollover by manipulating last_reset_day."""
        guard = CostGuard()
        guard._state = {}
        guard.set_budget("agent1", BudgetConfig(daily_limit_usd=10.0, monthly_limit_usd=100.0))
        guard.record("agent1", "task-1", 5.00)

        # Forcibly set last_reset_day to yesterday to trigger rollover
        state = guard._state["agent1"]
        state.last_reset_day = "2000-01-01"  # far in the past

        # Next check/record triggers rollover
        guard._maybe_rollover("agent1")
        assert guard._state["agent1"].daily_spent == 0.0

    def test_monthly_rollover_resets_monthly_spent(self, tmp_path):
        guard = CostGuard()
        guard._state = {}
        guard.set_budget("agent1", BudgetConfig(daily_limit_usd=10.0, monthly_limit_usd=100.0))
        guard.record("agent1", "task-1", 50.00)

        state = guard._state["agent1"]
        state.last_reset_month = "2000-01"

        guard._maybe_rollover("agent1")
        assert guard._state["agent1"].monthly_spent == 0.0

    def test_agent_summary_returns_expected_keys(self, tmp_path):
        guard = CostGuard()
        guard._state = {}
        guard.set_budget("agent1", BudgetConfig(daily_limit_usd=5.0, monthly_limit_usd=50.0))
        summary = guard.agent_summary("agent1")

        required_keys = {
            "agent_id", "daily_spent_usd", "monthly_spent_usd",
            "daily_limit_usd", "monthly_limit_usd",
            "avg_cost_per_call_7d", "recent_call_count",
        }
        assert required_keys.issubset(set(summary.keys()))
