"""
tests/work/test_work_loop.py — Unit tests for Work Operating Loop (§8.5).

Validates scripts/work_loop.py:
  - main() CLI: --mode read, --mode refresh, --state-dir, --quiet
  - main() exits 0 when no errors
  - main() missing --mode raises SystemExit(2)
  - run_read_loop() and run_refresh_loop() convenience factory functions
  - _update_learned_state() writes correct learning metrics to work-learned.md
  - Learning phase computation: calibration / prediction / anticipation
  - Atomic write: tmp file is cleaned up after update

Run: pytest tests/work/test_work_loop.py -v
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Inject scripts into sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import work_loop  # type: ignore
from work_loop import (  # type: ignore
    WorkLoop,
    LoopMode,
    LoopResult,
    run_read_loop,
    run_refresh_loop,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _inject_state(tmp_path: Path) -> Path:
    """Point work_loop module at a temp state/work directory."""
    state = tmp_path / "state" / "work"
    state.mkdir(parents=True, exist_ok=True)
    (state / "eval").mkdir(exist_ok=True)
    work_loop._WORK_STATE_DIR = state
    work_loop._AUDIT_FILE = state / "work-audit.md"
    work_loop._SUMMARY_FILE = state / "work-summary.md"
    work_loop._METRICS_FILE = state / "eval" / "work-metrics.json"
    # Fast-fail provider timeout for tests: prevents real CLI tools (agency,
    # npx, az) from blocking the test suite for their full 60s timeout.
    work_loop._DEFAULT_PROVIDER_TIMEOUT_SEC = 2
    return state


def _write_learned(state: Path, days_since: int = 0) -> Path:
    """Write a minimal work-learned.md seed in the state dir."""
    # Compute a bootstrap timestamp matching the given days_since
    bootstrap_dt = datetime.now(timezone.utc) - timedelta(days=days_since)
    summary_content = (
        f"---\nschema_version: '1.0'\n"
        f"last_updated: '{bootstrap_dt.isoformat()}'\n---\n\n# Summary\n"
    )
    (state / "work-summary.md").write_text(summary_content, encoding="utf-8")

    learned_content = (
        "---\n"
        "schema_version: '1.0'\n"
        "domain: work-learned\n"
        "last_updated: null\n"
        "generated_by: work_loop\n"
        "learning_phase: calibration\n"
        "days_since_bootstrap: 0\n"
        "refresh_runs: 0\n"
        "---\n\n"
        "## Sender Importance Model\n- pending\n"
    )
    p = state / "work-learned.md"
    p.write_text(learned_content, encoding="utf-8")
    return p


def _write_metrics(state: Path, refresh_count: int = 3) -> None:
    """Write a minimal work-metrics.json with refresh run records."""
    runs = [{"run_id": f"run_{i}", "mode": "refresh", "providers": [], "degraded": [], "errors": 0, "stages": []}
            for i in range(refresh_count)]
    runs += [{"run_id": "run_read", "mode": "read", "providers": [], "degraded": [], "errors": 0, "stages": []}]
    data = {"schema_version": "1.0", "last_updated": datetime.now(timezone.utc).isoformat(), "runs": runs}
    metrics_dir = state / "eval"
    metrics_dir.mkdir(exist_ok=True)
    (metrics_dir / "work-metrics.json").write_text(json.dumps(data), encoding="utf-8")


# ===========================================================================
# main() — CLI dispatch
# ===========================================================================

class TestWorkLoopCLI:

    def test_main_exists(self):
        """main() must be importable and callable."""
        assert callable(main)

    def test_main_mode_read_exits_zero(self, tmp_path, capsys):
        _inject_state(tmp_path)
        rc = main(["--mode", "read"])
        assert rc == 0

    def test_main_mode_refresh_exits_zero(self, tmp_path, capsys):
        _inject_state(tmp_path)
        rc = main(["--mode", "refresh"])
        assert rc == 0

    def test_main_quiet_suppresses_stdout(self, tmp_path, capsys):
        _inject_state(tmp_path)
        rc = main(["--mode", "read", "--quiet"])
        captured = capsys.readouterr()
        assert captured.out == ""
        assert rc == 0

    def test_main_without_quiet_prints_summary(self, tmp_path, capsys):
        _inject_state(tmp_path)
        main(["--mode", "read"])
        captured = capsys.readouterr()
        assert "Work loop" in captured.out
        assert "complete" in captured.out

    def test_main_missing_mode_raises_system_exit(self, tmp_path):
        _inject_state(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 2  # argparse error

    def test_main_invalid_mode_raises_system_exit(self, tmp_path):
        _inject_state(tmp_path)
        with pytest.raises(SystemExit):
            main(["--mode", "bogus"])

    def test_main_state_dir_override(self, tmp_path, capsys):
        state = _inject_state(tmp_path)
        rc = main(["--mode", "read", "--state-dir", str(state)])
        assert rc == 0

    def test_main_m_shorthand(self, tmp_path, capsys):
        _inject_state(tmp_path)
        rc = main(["-m", "read", "--quiet"])
        assert rc == 0

    def test_main_q_shorthand(self, tmp_path, capsys):
        _inject_state(tmp_path)
        rc = main(["-m", "read", "-q"])
        captured = capsys.readouterr()
        assert captured.out == ""
        assert rc == 0


# ===========================================================================
# run_read_loop / run_refresh_loop convenience factories
# ===========================================================================

class TestConvenienceFactories:

    def test_run_read_loop_returns_loop_result(self, tmp_path):
        _inject_state(tmp_path)
        result = run_read_loop()
        assert isinstance(result, LoopResult)

    def test_run_refresh_loop_returns_loop_result(self, tmp_path):
        _inject_state(tmp_path)
        result = run_refresh_loop()
        assert isinstance(result, LoopResult)

    def test_run_read_loop_mode_is_read(self, tmp_path):
        _inject_state(tmp_path)
        result = run_read_loop()
        assert result.mode == LoopMode.READ

    def test_run_refresh_loop_mode_is_refresh(self, tmp_path):
        _inject_state(tmp_path)
        result = run_refresh_loop()
        assert result.mode == LoopMode.REFRESH


# ===========================================================================
# _update_learned_state — §8.8 learning model persistence
# ===========================================================================

class TestUpdateLearnedState:

    def test_updates_last_updated_timestamp(self, tmp_path):
        state = _inject_state(tmp_path)
        _write_learned(state, days_since=0)
        _write_metrics(state, refresh_count=2)

        loop = WorkLoop(mode=LoopMode.REFRESH)
        loop._update_learned_state()

        import yaml  # type: ignore
        text = (state / "work-learned.md").read_text(encoding="utf-8")
        end = text.find("---", 3)
        fm = yaml.safe_load(text[3:end])
        assert fm["last_updated"] is not None
        assert fm["last_updated"] != "null"

    def test_calibration_phase_when_day_0(self, tmp_path):
        state = _inject_state(tmp_path)
        _write_learned(state, days_since=0)
        _write_metrics(state)

        loop = WorkLoop(mode=LoopMode.REFRESH)
        loop._update_learned_state()

        import yaml  # type: ignore
        text = (state / "work-learned.md").read_text(encoding="utf-8")
        end = text.find("---", 3)
        fm = yaml.safe_load(text[3:end])
        assert fm["learning_phase"] == "calibration"

    def test_prediction_phase_when_day_35(self, tmp_path):
        state = _inject_state(tmp_path)
        _write_learned(state, days_since=35)
        _write_metrics(state)

        loop = WorkLoop(mode=LoopMode.REFRESH)
        loop._update_learned_state()

        import yaml  # type: ignore
        text = (state / "work-learned.md").read_text(encoding="utf-8")
        end = text.find("---", 3)
        fm = yaml.safe_load(text[3:end])
        assert fm["learning_phase"] == "prediction"

    def test_anticipation_phase_when_day_65(self, tmp_path):
        state = _inject_state(tmp_path)
        _write_learned(state, days_since=65)
        _write_metrics(state)

        loop = WorkLoop(mode=LoopMode.REFRESH)
        loop._update_learned_state()

        import yaml  # type: ignore
        text = (state / "work-learned.md").read_text(encoding="utf-8")
        end = text.find("---", 3)
        fm = yaml.safe_load(text[3:end])
        assert fm["learning_phase"] == "anticipation"

    def test_refresh_runs_count_is_correct(self, tmp_path):
        state = _inject_state(tmp_path)
        _write_learned(state, days_since=0)
        _write_metrics(state, refresh_count=7)

        loop = WorkLoop(mode=LoopMode.REFRESH)
        loop._update_learned_state()

        import yaml  # type: ignore
        text = (state / "work-learned.md").read_text(encoding="utf-8")
        end = text.find("---", 3)
        fm = yaml.safe_load(text[3:end])
        assert fm["refresh_runs"] == 7

    def test_body_content_preserved_after_update(self, tmp_path):
        state = _inject_state(tmp_path)
        _write_learned(state, days_since=5)
        _write_metrics(state)

        loop = WorkLoop(mode=LoopMode.REFRESH)
        loop._update_learned_state()

        text = (state / "work-learned.md").read_text(encoding="utf-8")
        assert "## Sender Importance Model" in text
        assert "pending" in text

    def test_no_learned_file_does_not_raise(self, tmp_path):
        state = _inject_state(tmp_path)
        # No work-learned.md — must not raise
        loop = WorkLoop(mode=LoopMode.REFRESH)
        loop._update_learned_state()  # should be a no-op

    def test_no_tmp_file_left_after_update(self, tmp_path):
        state = _inject_state(tmp_path)
        _write_learned(state, days_since=0)
        _write_metrics(state)

        loop = WorkLoop(mode=LoopMode.REFRESH)
        loop._update_learned_state()

        # No .md.tmp file should remain
        assert not (state / "work-learned.md.tmp").exists()

    def test_learn_stage_only_runs_on_refresh(self, tmp_path):
        """_update_learned_state must NOT fire on READ mode loops (no new data)."""
        state = _inject_state(tmp_path)
        _write_learned(state, days_since=0)

        # Run a READ loop — should not update work-learned.md
        before = (state / "work-learned.md").read_text(encoding="utf-8")
        loop = WorkLoop(mode=LoopMode.READ)
        loop._stage_learn_async()
        after = (state / "work-learned.md").read_text(encoding="utf-8")

        assert before == after  # unchanged

    def test_days_since_bootstrap_is_non_negative(self, tmp_path):
        state = _inject_state(tmp_path)
        _write_learned(state, days_since=10)
        _write_metrics(state)

        loop = WorkLoop(mode=LoopMode.REFRESH)
        loop._update_learned_state()

        import yaml  # type: ignore
        text = (state / "work-learned.md").read_text(encoding="utf-8")
        end = text.find("---", 3)
        fm = yaml.safe_load(text[3:end])
        assert fm["days_since_bootstrap"] >= 0


# ===========================================================================
# Career velocity + meeting quality signals (§8.8 v2.3.0)
# ===========================================================================

class TestCareerVelocitySignals:
    """career_velocity_trajectory and career_velocity_events_90d written to frontmatter."""

    def test_trajectory_field_written(self, tmp_path):
        state = _inject_state(tmp_path)
        _write_learned(state, days_since=10)
        _write_metrics(state)

        loop = WorkLoop(mode=LoopMode.REFRESH)
        loop._update_learned_state()

        import yaml  # type: ignore
        text = (state / "work-learned.md").read_text(encoding="utf-8")
        end = text.find("---", 3)
        fm = yaml.safe_load(text[3:end])
        assert "career_velocity_trajectory" in fm
        assert fm["career_velocity_trajectory"] in ("expanding", "stable")

    def test_events_90d_field_non_negative(self, tmp_path):
        state = _inject_state(tmp_path)
        _write_learned(state, days_since=10)
        _write_metrics(state)

        loop = WorkLoop(mode=LoopMode.REFRESH)
        loop._update_learned_state()

        import yaml  # type: ignore
        text = (state / "work-learned.md").read_text(encoding="utf-8")
        end = text.find("---", 3)
        fm = yaml.safe_load(text[3:end])
        assert fm.get("career_velocity_events_90d", -1) >= 0

    def test_stable_trajectory_with_no_journeys_file(self, tmp_path):
        state = _inject_state(tmp_path)
        _write_learned(state, days_since=10)
        _write_metrics(state)
        # No work-project-journeys.md — should default to stable

        loop = WorkLoop(mode=LoopMode.REFRESH)
        loop._update_learned_state()

        import yaml  # type: ignore
        text = (state / "work-learned.md").read_text(encoding="utf-8")
        end = text.find("---", 3)
        fm = yaml.safe_load(text[3:end])
        assert fm["career_velocity_trajectory"] == "stable"


class TestMeetingQualitySignals:
    """meeting_quality_strategic_fraction and meeting_quality_total written to frontmatter."""

    def test_meeting_quality_fields_written(self, tmp_path):
        state = _inject_state(tmp_path)
        _write_learned(state, days_since=10)
        _write_metrics(state)

        loop = WorkLoop(mode=LoopMode.REFRESH)
        loop._update_learned_state()

        import yaml  # type: ignore
        text = (state / "work-learned.md").read_text(encoding="utf-8")
        end = text.find("---", 3)
        fm = yaml.safe_load(text[3:end])
        assert "meeting_quality_strategic_fraction" in fm
        assert "meeting_quality_total" in fm

    def test_fraction_is_between_0_and_1(self, tmp_path):
        state = _inject_state(tmp_path)
        _write_learned(state, days_since=10)
        _write_metrics(state)

        loop = WorkLoop(mode=LoopMode.REFRESH)
        loop._update_learned_state()

        import yaml  # type: ignore
        text = (state / "work-learned.md").read_text(encoding="utf-8")
        end = text.find("---", 3)
        fm = yaml.safe_load(text[3:end])
        frac = fm.get("meeting_quality_strategic_fraction", 0.0)
        assert 0.0 <= frac <= 1.0

    def test_total_meetings_zero_with_no_calendar_file(self, tmp_path):
        state = _inject_state(tmp_path)
        _write_learned(state, days_since=10)
        _write_metrics(state)
        # No work-calendar.md — total should be 0

        loop = WorkLoop(mode=LoopMode.REFRESH)
        loop._update_learned_state()

        import yaml  # type: ignore
        text = (state / "work-learned.md").read_text(encoding="utf-8")
        end = text.find("---", 3)
        fm = yaml.safe_load(text[3:end])
        assert fm.get("meeting_quality_total", 0) == 0
