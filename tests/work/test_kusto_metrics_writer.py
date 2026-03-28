"""
Tests for write_kusto_metrics_state() and its extractor helpers.

Covers: _extract_fleet_size, _extract_velocity, _extract_throughput,
_extract_incidents, _kusto_ts, write_kusto_metrics_state.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from work_domain_writers import (
    _extract_fleet_size,
    _extract_incidents,
    _extract_throughput,
    _extract_velocity,
    _kusto_ts,
    write_kusto_metrics_state,
)

# ---------------------------------------------------------------------------
# _kusto_ts
# ---------------------------------------------------------------------------


class TestKustoTs:
    def test_returns_compact_date(self):
        ts = _kusto_ts()
        assert "/" in ts
        parts = ts.split("/")
        assert len(parts) == 2
        month, day = int(parts[0]), int(parts[1])
        assert 1 <= month <= 12
        assert 1 <= day <= 31

    def test_no_leading_zeros(self):
        """Cross-platform: no zero-padding (the whole reason we don't use strftime)."""
        ts = _kusto_ts()
        month, day = ts.split("/")
        # Leading zeros would mean '01' instead of '1'
        if int(month) < 10:
            assert not month.startswith("0")
        if int(day) < 10:
            assert not day.startswith("0")


# ---------------------------------------------------------------------------
# _extract_fleet_size
# ---------------------------------------------------------------------------


class TestExtractFleetSize:
    def test_standard_row(self):
        rows = [{"Clusters": 90, "Tenants": 455, "DataCenters": 98, "DD_PF_Tenants": 12}]
        result = _extract_fleet_size(rows)
        assert len(result) == 1
        metric_id, val = result[0]
        assert metric_id == "M01"
        assert "90 clusters" in val
        assert "455 tenants" in val
        assert "12 DD-PF" in val
        assert "Kusto live" in val

    def test_without_ddpf(self):
        rows = [{"Clusters": 64, "Tenants": 131, "DataCenters": 72}]
        result = _extract_fleet_size(rows)
        assert "DD-PF" not in result[0][1]

    def test_lowercase_keys(self):
        rows = [{"clusters": 50, "tenants": 100, "datacenters": 40}]
        result = _extract_fleet_size(rows)
        assert len(result) == 1
        assert "50 clusters" in result[0][1]

    def test_empty_rows(self):
        assert _extract_fleet_size([]) == []

    def test_none_rows(self):
        assert _extract_fleet_size(None) == []


# ---------------------------------------------------------------------------
# _extract_velocity
# ---------------------------------------------------------------------------


class TestExtractVelocity:
    def test_p50_and_p90(self):
        rows = [
            {"Percentile": "P50", "Hours": 4.2},
            {"Percentile": "P75", "Hours": 8.1},
            {"Percentile": "P90", "Hours": 14.5},
        ]
        result = _extract_velocity(rows)
        ids = {r[0] for r in result}
        assert "M04" in ids  # P50
        assert "M05" in ids  # P90
        for mid, val in result:
            assert "Kusto live" in val

    def test_missing_percentiles(self):
        rows = [{"Percentile": "P75", "Hours": 8}]
        result = _extract_velocity(rows)
        assert result == []  # Neither P50 nor P90

    def test_empty_rows(self):
        assert _extract_velocity([]) == []


# ---------------------------------------------------------------------------
# _extract_throughput
# ---------------------------------------------------------------------------


class TestExtractThroughput:
    def test_standard(self):
        rows = [{"TotalDeploys": 245}]
        result = _extract_throughput(rows)
        assert len(result) == 1
        assert result[0][0] == "M06"
        assert "245" in result[0][1]

    def test_alt_key(self):
        rows = [{"Deploys": 100}]
        result = _extract_throughput(rows)
        assert "100" in result[0][1]

    def test_empty(self):
        assert _extract_throughput([]) == []


# ---------------------------------------------------------------------------
# _extract_incidents
# ---------------------------------------------------------------------------


class TestExtractIncidents:
    def test_multiple_severities(self):
        rows = [
            {"Severity": 0},
            {"Severity": 1},
            {"Severity": 1},
            {"Severity": 2},
        ]
        result = _extract_incidents(rows)
        assert len(result) == 1
        assert result[0][0] == "M20"
        assert "4 active" in result[0][1]

    def test_empty_rows_means_zero(self):
        result = _extract_incidents([])
        assert len(result) == 1
        assert "0 active" in result[0][1]


# ---------------------------------------------------------------------------
# write_kusto_metrics_state
# ---------------------------------------------------------------------------

_SAMPLE_MD = """\
---
title: XPF Program Structure
---

## Signal Summary

| Signal | Count | Description |
|--------|-------|-------------|
| 🔴 Red | 7 | Needs action |
| 🟡 Yellow | 24 | Monitor |
| 🟢 Green | 7 | On track |

### WS1 — Fleet Convergence

| ID | Metric Name | Current Value | Target | Signal | Source |
|----|-------------|---------------|--------|--------|--------|
| M01 | Fleet Size | **60 clusters** old | 75 clusters | 🟡 | Manual |
| M04 | Deploy Velocity P50 | **5 hrs** old | 4 hrs | 🟡 | Manual |
| M05 | Deploy Velocity P90 | **16 hrs** old | 12 hrs | 🔴 | Manual |
| M06 | Deploy Throughput | **200 deploys/7d** | 300 | 🟡 | Manual |
| M20 | Active Incidents | **3 active** old | 0 | 🔴 | Manual |
"""


class TestWriteKustoMetricsState:
    def test_updates_metrics_in_place(self, tmp_path):
        dest = tmp_path / "xpf-program-structure.md"
        dest.write_text(_SAMPLE_MD, encoding="utf-8")

        kusto_data = {
            "GQ-001": {
                "rows": [{"Clusters": 64, "Tenants": 131, "DataCenters": 72, "TotalEnNodes": 12702}],
                "card": None,
            },
            "GQ-012": {
                "rows": [
                    {"Percentile": "P50", "Hours": 4},
                    {"Percentile": "P90", "Hours": 14},
                ],
                "card": None,
            },
        }
        write_kusto_metrics_state(kusto_data, dest)
        content = dest.read_text(encoding="utf-8")

        assert "64 clusters" in content
        assert "131 tenants" in content
        assert "60 clusters" not in content  # Old value replaced

    def test_skips_missing_file(self, tmp_path, caplog):
        dest = tmp_path / "nonexistent.md"
        write_kusto_metrics_state({"GQ-001": {"rows": [], "card": None}}, dest)
        # Should not raise, just warn

    def test_no_update_on_unknown_query(self, tmp_path):
        dest = tmp_path / "xpf-program-structure.md"
        dest.write_text(_SAMPLE_MD, encoding="utf-8")
        original = dest.read_text(encoding="utf-8")

        write_kusto_metrics_state({"GQ-999": {"rows": [{"x": 1}], "card": None}}, dest)
        assert dest.read_text(encoding="utf-8") == original

    def test_extractor_failure_is_isolated(self, tmp_path):
        """If one extractor throws, others still run."""
        dest = tmp_path / "xpf-program-structure.md"
        dest.write_text(_SAMPLE_MD, encoding="utf-8")

        kusto_data = {
            "GQ-001": {"rows": [{"Clusters": 70, "Tenants": 140, "DataCenters": 80, "TotalEnNodes": 15000}], "card": None},
            "GQ-010": {"rows": [{"TotalDeploys": 300}], "card": None},
        }

        with patch("work_domain_writers._extract_fleet_size", side_effect=ValueError("boom")):
            write_kusto_metrics_state(kusto_data, dest)

        content = dest.read_text(encoding="utf-8")
        # GQ-010 should still have updated M06
        assert "300 deploys/7d" in content

    def test_empty_kusto_data(self, tmp_path):
        dest = tmp_path / "xpf-program-structure.md"
        dest.write_text(_SAMPLE_MD, encoding="utf-8")
        original = dest.read_text(encoding="utf-8")

        write_kusto_metrics_state({}, dest)
        assert dest.read_text(encoding="utf-8") == original
