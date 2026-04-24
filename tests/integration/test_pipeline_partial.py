"""
tests/integration/test_pipeline_partial.py — Integration tests for partial write+assemble.
specs/steal.md §15.4.1

Verifies that when one connector fails (status='error'), write_partial() +
assemble_partials() together produce a partial briefing from the remaining
ok connectors — no exceptions raised, and warnings identify the failed provider.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts" / "lib"))

from partial_writer import PartialResult, assemble_partials, write_partial


class TestPipelinePartial:
    """Simulate one failing connector in a multi-connector pipeline run."""

    def test_one_failed_connector_produces_partial_briefing(self, tmp_path):
        """When one connector errors, assemble still returns data from ok connectors."""
        run_id = "integration-run-001"

        # Connector A: succeeded
        write_partial(
            tmp_path,
            PartialResult(
                run_id=run_id,
                provider="ado",
                timestamp="2026-01-01T00:00:00+00:00",
                status="ok",
                data={"ado_items": ["work-item-1"]},
            ),
        )

        # Connector B: failed (error)
        write_partial(
            tmp_path,
            PartialResult(
                run_id=run_id,
                provider="outlook",
                timestamp="2026-01-01T00:00:00+00:00",
                status="error",
                data={},
                error="auth timeout after 30s",
            ),
        )

        # Connector C: succeeded
        write_partial(
            tmp_path,
            PartialResult(
                run_id=run_id,
                provider="teams",
                timestamp="2026-01-01T00:00:00+00:00",
                status="ok",
                data={"teams_messages": ["msg-42"]},
            ),
        )

        merged, warnings = assemble_partials(tmp_path, run_id)

        # Both ok connectors included
        assert "ado" in merged
        assert "teams" in merged

        # Failed connector excluded from merged data
        assert "outlook" not in merged

        # Warning issued for the failed provider
        assert any("outlook" in w for w in warnings)

        # Partial briefing is non-empty (at least one ok connector)
        assert len(merged) >= 1

    def test_all_connectors_ok_no_warnings(self, tmp_path):
        run_id = "integration-run-002"

        for provider in ("ado", "outlook", "teams"):
            write_partial(
                tmp_path,
                PartialResult(
                    run_id=run_id,
                    provider=provider,
                    timestamp="2026-01-01T00:00:00+00:00",
                    status="ok",
                    data={f"{provider}_data": True},
                ),
            )

        merged, warnings = assemble_partials(tmp_path, run_id)

        assert set(merged.keys()) == {"ado", "outlook", "teams"}
        # No skip warnings expected when all succeed
        assert not any("Skipped provider" in w for w in warnings)

    def test_all_connectors_failed_gives_empty_merged_with_warnings(self, tmp_path):
        run_id = "integration-run-003"

        for provider in ("ado", "outlook"):
            write_partial(
                tmp_path,
                PartialResult(
                    run_id=run_id,
                    provider=provider,
                    timestamp="2026-01-01T00:00:00+00:00",
                    status="error",
                    data={},
                    error="all down",
                ),
            )

        merged, warnings = assemble_partials(tmp_path, run_id)

        assert merged == {}
        assert len(warnings) >= 2  # one per skipped provider
