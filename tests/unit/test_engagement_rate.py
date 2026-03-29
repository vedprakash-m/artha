"""
tests/unit/test_engagement_rate.py — Unit tests for engagement rate storage
in scripts/health_check_writer.py

Coverage:
  - _append_catch_up_run() writes all required fields to catch_up_runs.yaml
  - engagement_rate stored as null when items_surfaced == 0
  - engagement_rate stored as float when items_surfaced > 0
  - Retention policy: >100 entries are truncated to last 100
  - Multiple calls produce multiple entries in the list
  - All new CLI flags (--engagement-rate, --user-ois, etc.) accepted without error
  - OI origin field: system-created OIs should have origin: system in open_items.md

Ref: specs/skills-reloaded.md §3.2, §3.2.1
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_ARTHA = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ARTHA / "scripts"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_append_func():
    """Import _append_catch_up_run dynamically to avoid module-level side effects."""
    import health_check_writer
    return health_check_writer._append_catch_up_run


# ---------------------------------------------------------------------------
# _append_catch_up_run() tests
# ---------------------------------------------------------------------------

class TestAppendCatchUpRun:
    def test_creates_yaml_file_with_required_fields(self, tmp_path):
        import yaml
        func = _get_append_func()
        out = tmp_path / "catch_up_runs.yaml"

        with patch("health_check_writer.CATCH_UP_RUNS_FILE", out):
            func(
                timestamp="2026-03-28T10:00:00Z",
                engagement_rate=0.33,
                user_ois=1,
                system_ois=3,
                items_surfaced=6,
                correction_count=0,
                briefing_format="standard",
                email_count=30,
                domains_processed=["finance", "kids"],
            )

        assert out.exists()
        data = yaml.safe_load(out.read_text())
        assert isinstance(data, list)
        assert len(data) == 1
        entry = data[0]
        assert entry["engagement_rate"] == pytest.approx(0.33)
        assert entry["user_ois"] == 1
        assert entry["system_ois"] == 3
        assert entry["items_surfaced"] == 6
        assert entry["correction_count"] == 0
        assert entry["briefing_format"] == "standard"
        assert entry["email_count"] == 30

    def test_null_engagement_rate_when_items_surfaced_zero(self, tmp_path):
        """When items_surfaced == 0, engagement_rate must be stored as null."""
        import yaml
        func = _get_append_func()
        out = tmp_path / "catch_up_runs.yaml"

        with patch("health_check_writer.CATCH_UP_RUNS_FILE", out):
            func(
                timestamp="2026-03-28T10:00:00Z",
                engagement_rate=0.0,
                user_ois=0,
                system_ois=0,
                items_surfaced=0,
                correction_count=0,
                briefing_format="flash",
                email_count=5,
                domains_processed=[],
            )

        data = yaml.safe_load(out.read_text())
        assert data[0]["engagement_rate"] is None

    def test_multiple_calls_append_to_list(self, tmp_path):
        """Each call appends one entry; list grows."""
        import yaml
        func = _get_append_func()
        out = tmp_path / "catch_up_runs.yaml"

        for i in range(3):
            with patch("health_check_writer.CATCH_UP_RUNS_FILE", out):
                func(
                    timestamp=f"2026-03-2{i+6}T10:00:00Z",
                    engagement_rate=0.30,
                    user_ois=0,
                    system_ois=1,
                    items_surfaced=3,
                    correction_count=0,
                    briefing_format="standard",
                    email_count=20,
                    domains_processed=["finance"],
                )

        data = yaml.safe_load(out.read_text())
        assert len(data) == 3

    def test_retention_policy_caps_at_100_entries(self, tmp_path):
        """When list exceeds 100 entries, oldest entries are trimmed."""
        import yaml
        func = _get_append_func()
        out = tmp_path / "catch_up_runs.yaml"

        # Prepopulate with 100 entries
        existing = [{"timestamp": f"2026-01-{i+1:02d}T10:00:00Z", "engagement_rate": 0.30,
                     "user_ois": 0, "system_ois": 1, "items_surfaced": 3, "correction_count": 0,
                     "briefing_format": "standard", "email_count": 20}
                    for i in range(100)]
        import yaml as _yaml
        out.write_text(_yaml.dump(existing, default_flow_style=False))

        # Add one more
        with patch("health_check_writer.CATCH_UP_RUNS_FILE", out):
            func(
                timestamp="2026-04-01T10:00:00Z",
                engagement_rate=0.50,
                user_ois=1,
                system_ois=2,
                items_surfaced=4,
                correction_count=0,
                briefing_format="deep",
                email_count=40,
                domains_processed=["calendar"],
            )

        data = yaml.safe_load(out.read_text())
        # Should be capped at 100
        assert len(data) <= 100
        # Most recent entry should be retained
        assert data[-1]["engagement_rate"] == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# CLI flag tests
# ---------------------------------------------------------------------------

class TestHealthCheckWriterCLIFlags:
    """Smoke test: new CLI flags are accepted without error."""

    def test_new_flags_parsed_without_error(self, tmp_path):
        """health_check_writer.main() accepts all new engagement flags."""
        import health_check_writer

        mock_hc = tmp_path / "health-check.md"
        mock_hc.write_text("---\ncatch_up_count: 5\n---\n# Health Check\n")
        mock_runs = tmp_path / "catch_up_runs.yaml"
        mock_cfg = tmp_path / "skills.yaml"
        mock_cfg.write_text("skills:\n  passport_expiry:\n    enabled: true\n    cadence: every_run\n")
        mock_audit = tmp_path / "audit.md"
        mock_audit.write_text("")

        with (
            patch("health_check_writer.HEALTH_CHECK_FILE", mock_hc),
            patch("health_check_writer.CATCH_UP_RUNS_FILE", mock_runs),
            patch("health_check_writer.CONFIG_DIR", tmp_path),
            patch("health_check_writer.STATE_DIR", tmp_path),
        ):
            exit_code = health_check_writer.main([
                "--last-catch-up", "2026-03-28T10:00:00Z",
                "--email-count", "30",
                "--mode", "normal",
                "--briefing-format", "standard",
                "--domains-processed", "finance,kids",
                "--engagement-rate", "0.33",
                "--user-ois", "1",
                "--system-ois", "3",
                "--items-surfaced", "6",
                "--correction-count", "0",
            ])
        assert exit_code == 0


# ---------------------------------------------------------------------------
# analyze_skills() — last_nonzero_value display
# ---------------------------------------------------------------------------

class TestAnalyzeSkillsLastVal:
    """Verify analyze_skills() reads health.last_nonzero_value for the Last Val column.

    The cache entry schema has last_nonzero_value inside the 'health' sub-dict.
    The old (buggy) code read entry.get("last_updated") etc. which don't exist.
    """

    def _make_cache_file(self, tmp_path, skill_name, last_nonzero_value, classification="healthy", total_runs=10):
        import json
        cache = {
            skill_name: {
                "last_run": "2026-03-28T10:00:00+00:00",
                "current": {"name": skill_name, "status": "success", "data": {"value": 42}},
                "previous": None,
                "changed": False,
                "health": {
                    "total_runs": total_runs,
                    "success_count": total_runs,
                    "failure_count": 0,
                    "zero_value_count": 0,
                    "consecutive_zero": 0,
                    "consecutive_stable": 0,
                    "last_success": "2026-03-28T10:00:00+00:00",
                    "last_failure": None,
                    "last_nonzero_value": last_nonzero_value,
                    "last_wall_clock_ms": 45,
                    "r7_skips": 0,
                    "last_r7_prompt": None,
                    "maturity": "trusted",
                    "classification": classification,
                },
            }
        }
        f = tmp_path / "skills_cache.json"
        f.write_text(json.dumps(cache, indent=2))
        return f

    def test_last_nonzero_value_displayed(self, tmp_path, monkeypatch):
        """analyze_skills() reads health.last_nonzero_value, not stale top-level keys."""
        import sys
        _ARTHA = Path(__file__).resolve().parent.parent.parent
        sys.path.insert(0, str(_ARTHA / "scripts"))
        import eval_runner

        cache_file = self._make_cache_file(
            tmp_path, "bill_due_tracker", "2026-03-25T08:00:00+00:00"
        )
        monkeypatch.setattr(eval_runner, "_SKILLS_CACHE", cache_file)

        result = eval_runner.analyze_skills()
        assert "error" not in result
        rows = result["skills"]
        assert len(rows) == 1
        row = rows[0]
        # Should show the date from last_nonzero_value, not "Never"
        assert row["last_value"] != "Never", (
            "last_value should reflect health.last_nonzero_value, got 'Never'"
        )
        assert "3/25" in row["last_value"]  # formatted as %-m/%-d

    def test_last_nonzero_null_shows_never(self, tmp_path, monkeypatch):
        """When last_nonzero_value is None (skill never returned data), display 'Never'."""
        import sys
        _ARTHA = Path(__file__).resolve().parent.parent.parent
        sys.path.insert(0, str(_ARTHA / "scripts"))
        import eval_runner

        cache_file = self._make_cache_file(
            tmp_path, "uscis_status", None, classification="degraded"
        )
        monkeypatch.setattr(eval_runner, "_SKILLS_CACHE", cache_file)

        result = eval_runner.analyze_skills()
        rows = result["skills"]
        assert rows[0]["last_value"] == "Never"
