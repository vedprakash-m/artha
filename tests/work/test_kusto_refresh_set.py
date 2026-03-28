"""
Tests for run_refresh_set() and REFRESH_QUERY_IDS in kusto_runner.py.

All Kusto calls are mocked — no live cluster access needed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from kusto_runner import REFRESH_QUERY_IDS, run_refresh_set


class TestRefreshQueryIds:
    def test_contains_expected_ids(self):
        assert "GQ-001" in REFRESH_QUERY_IDS
        assert "GQ-010" in REFRESH_QUERY_IDS
        assert "GQ-012" in REFRESH_QUERY_IDS
        assert "GQ-050" in REFRESH_QUERY_IDS

    def test_all_ids_are_strings(self):
        for qid in REFRESH_QUERY_IDS:
            assert isinstance(qid, str)
            assert qid.startswith("GQ-")


class TestRunRefreshSet:
    """run_refresh_set with mocked parse_registry and KustoRunner."""

    @pytest.fixture(autouse=True)
    def mock_kusto(self):
        """Patch parse_registry and KustoRunner to avoid real Kusto calls."""
        fake_registry = {
            "GQ-001": {"id": "GQ-001", "kql": "...", "cluster": "...", "database": "..."},
            "GQ-010": {"id": "GQ-010", "kql": "...", "cluster": "...", "database": "..."},
            "GQ-012": {"id": "GQ-012", "kql": "...", "cluster": "...", "database": "..."},
            "GQ-050": {"id": "GQ-050", "kql": "...", "cluster": "...", "database": "..."},
        }

        mock_runner_instance = MagicMock()
        mock_runner_instance.run_golden_query.return_value = (
            [{"Clusters": 64, "Tenants": 131}],
            MagicMock(),  # DataCard
        )

        with (
            patch("kusto_runner.parse_registry", return_value=fake_registry) as self.mock_parse,
            patch("kusto_runner.KustoRunner", return_value=mock_runner_instance) as self.mock_cls,
        ):
            self.mock_runner = mock_runner_instance
            yield

    def test_returns_dict_keyed_by_query_id(self):
        result = run_refresh_set(query_ids=["GQ-001", "GQ-010"])
        assert isinstance(result, dict)
        assert "GQ-001" in result
        assert "GQ-010" in result

    def test_successful_query_has_rows(self):
        result = run_refresh_set(query_ids=["GQ-001"])
        assert result["GQ-001"]["rows"] == [{"Clusters": 64, "Tenants": 131}]
        assert result["GQ-001"]["error"] is None

    def test_missing_query_returns_error(self):
        result = run_refresh_set(query_ids=["GQ-999"])
        assert result["GQ-999"]["rows"] == []
        assert "Not found" in result["GQ-999"]["error"]

    def test_query_exception_is_isolated(self):
        self.mock_runner.run_golden_query.side_effect = [
            ([{"Clusters": 64}], MagicMock()),  # GQ-001 succeeds
            RuntimeError("Kusto timeout"),  # GQ-010 fails
        ]
        result = run_refresh_set(query_ids=["GQ-001", "GQ-010"])
        assert result["GQ-001"]["error"] is None
        assert "timeout" in result["GQ-010"]["error"].lower()

    def test_defaults_to_refresh_query_ids(self):
        result = run_refresh_set()
        # Should attempt all REFRESH_QUERY_IDS
        for qid in REFRESH_QUERY_IDS:
            assert qid in result

    def test_custom_registry_path(self):
        run_refresh_set(query_ids=["GQ-001"], registry_path="/custom/path.md")
        self.mock_parse.assert_called_once_with("/custom/path.md")

    def test_error_message_truncated(self):
        self.mock_runner.run_golden_query.side_effect = RuntimeError("x" * 300)
        result = run_refresh_set(query_ids=["GQ-001"])
        assert len(result["GQ-001"]["error"]) <= 200
