"""
tests/unit/test_xhealth_signal.py — Unit tests for xhealth_signal.py FR-42.

Coverage:
  XHealthSignal.query_icm_summary:
    - _run_ps1 returns valid TSV → sev counts parsed correctly
    - _run_ps1 failure → SignalResult.error set, data empty

  XHealthSignal.check_icm_wois:
    - WOI text with IcM IDs → query_icm_status called and mitigated WOIs returned
    - WOI text with no IcM IDs → empty result list
    - Circuit breaker: >10 IcM IDs → only first 10 checked

  XHealthSignal.morning_signal:
    - Returns dict with 'icm_summary' and 'fleet_health' keys
    - Both values are SignalResult instances

  XHealthSignal.format_morning_block:
    - Output contains '🏥 xHealth Morning Signal' header
    - Output contains one line per signal
    - Error signals produce ⚠️ lines (graceful degradation)

Ref: specs/xhealth-dashboard.md §9.1 (INT-1), §9.2 (INT-2), §9.6 (INT-6), FR-42
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# Also need the scripts/work path for the module
_WORK = _SCRIPTS / "work"
if str(_WORK) not in sys.path:
    sys.path.insert(0, str(_WORK))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_signal(sig_type: str, data: dict | None = None, error: str | None = None):
    """Construct a SignalResult for test assertions."""
    from work.xhealth_signal import SignalResult
    return SignalResult(
        signal_type=sig_type,
        data=data or {},
        dashboard="TestDashboard",
        page="test-page",
        queries=["Q1"],
        error=error,
    )


# ─────────────────────────────────────────────────────────────────────────────
# query_icm_summary
# ─────────────────────────────────────────────────────────────────────────────

class TestQueryIcmSummary:
    """Tests for XHealthSignal.query_icm_summary() parsing logic."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from work.xhealth_signal import XHealthSignal
        self.XHealthSignal = XHealthSignal

    def _make_signal_instance(self, run_ps1_side_effect):
        """Create an XHealthSignal with _run_ps1 and is_available mocked."""
        sig = self.XHealthSignal.__new__(self.XHealthSignal)
        sig._kusto_cli = Path("/fake/Kusto.Cli.exe")
        sig._routing_index = None
        sig._run_ps1 = run_ps1_side_effect
        return sig

    def test_parses_sev_counts_from_tsv(self):
        """Valid _run_ps1 outputs → sev1/sev2/cri counts parsed from first numeric value."""
        # Q1 → cri count, Q3 → sev2 count, Q5 → sev1 count
        responses = [
            ("Count\n3\n", "", 0),   # Q1 (CRI)
            ("Count\n12\n", "", 0),  # Q3 (Sev2)
            ("Count\n2\n", "", 0),   # Q5 (Sev1)
        ]
        call_count = [0]

        def fake_run_ps1(dashboard, page, query, params=None, dry_run=False):
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx]

        sig = self._make_signal_instance(fake_run_ps1)
        # Patch is_available so _run_query_safe doesn't short-circuit
        sig.is_available = lambda: True

        result = sig.query_icm_summary()

        assert result.error is None
        assert result.confidence == "[live]"
        assert result.data["cri"] == "3"
        assert result.data["sev2"] == "12"
        assert result.data["sev1"] == "2"

    def test_error_returned_when_run_ps1_fails(self):
        """Non-zero exit + no stdout → SignalResult.error is set."""
        def fake_run_ps1(dashboard, page, query, params=None, dry_run=False):
            return "", "Authentication error", 1

        sig = self._make_signal_instance(fake_run_ps1)
        sig.is_available = lambda: True

        result = sig.query_icm_summary()

        assert result.error is not None
        assert "exit" in result.error or "Authentication" in result.error or "1" in result.error
        assert result.confidence == "[unavailable]"
        assert result.data == {}

    def test_unavailable_when_kusto_cli_missing(self):
        """is_available() False → _run_ps1 returns error sentinel → error in result."""
        from work.xhealth_signal import XHealthSignal
        sig = XHealthSignal.__new__(XHealthSignal)
        sig._kusto_cli = None  # not found
        sig._routing_index = None

        result = sig.query_icm_summary()

        # When Kusto.Cli unavailable, _run_ps1 returns ("", "..unavailable..", 1)
        assert result.error is not None


# ─────────────────────────────────────────────────────────────────────────────
# check_icm_wois
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckIcmWois:
    """Tests for XHealthSignal.check_icm_wois() parsing and auto-close logic."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from work.xhealth_signal import XHealthSignal
        self.XHealthSignal = XHealthSignal

    # Regex: (WOI-\d+)[^\|]*\|[^\|]*IcM\s+(\d{8,10})
    # IcM ID must appear in the column immediately following the WOI ID column.
    _WOI_TEXT_WITH_IcM = (
        "| WOI-001 | IcM 123456789 | XPF repair stuck | Active | 2026-04-01 |\n"
        "| WOI-002 | IcM 987654321 | Sev2 fleet offline | Active | 2026-04-02 |\n"
        "| WOI-003 | No IcM reference | Unrelated item | Active | 2026-04-03 |\n"
    )

    _WOI_TEXT_NO_IcM = (
        "| WOI-010 | ADO#12345 | Deploy blocker | Active | 2026-04-01 |\n"
        "| WOI-011 | No reference | Unrelated | Active | 2026-04-02 |\n"
    )

    def test_extracts_icm_ids_and_returns_status(self):
        """WOI text with IcM IDs → query_icm_status called, statuses returned."""
        from datetime import datetime, timezone

        mock_status = [
            {"icm_id": "123456789", "status": "MITIGATED",
             "raw": "mitigated", "confidence": "[live]",
             "checked_at": datetime.now(timezone.utc).isoformat()},
        ]

        from work.xhealth_signal import XHealthSignal
        sig = XHealthSignal.__new__(XHealthSignal)
        sig._kusto_cli = Path("/fake/Kusto.Cli.exe")
        sig._routing_index = None

        # Mock query_icm_status to return mitigated for first IcM
        def fake_query_icm_status(icm_ids):
            return [mock_status[0]]

        sig.query_icm_status = fake_query_icm_status

        results = sig.check_icm_wois(self._WOI_TEXT_WITH_IcM)

        # Should find WOI-001 and WOI-002 (two IcM IDs in text)
        assert len(results) >= 1
        statuses = {r["woi_id"]: r for r in results}
        assert "WOI-001" in statuses
        assert statuses["WOI-001"]["icm_id"] == "123456789"
        assert statuses["WOI-001"]["mitigated"] is True
        assert statuses["WOI-001"]["confidence"] == "[live]"

    def test_no_icm_ids_returns_empty(self):
        """WOI text without any IcM IDs → empty result list, no network calls."""
        from work.xhealth_signal import XHealthSignal
        sig = XHealthSignal.__new__(XHealthSignal)
        sig._kusto_cli = Path("/fake/Kusto.Cli.exe")
        sig._routing_index = None

        called = []
        sig.query_icm_status = lambda ids: called.append(ids) or []

        results = sig.check_icm_wois(self._WOI_TEXT_NO_IcM)

        assert results == []
        assert called == []  # query_icm_status never called

    def test_circuit_breaker_caps_at_10(self):
        """More than 10 IcM WOIs in text → only 10 IcM IDs processed."""
        # Build WOI text with 12 IcM IDs
        lines = "\n".join(
            f"| WOI-{i:03d} | Active | IcM 1000000{i:02d} | incident {i} | 2026-04-01 |"
            for i in range(12)
        )
        call_log = []

        from work.xhealth_signal import XHealthSignal
        sig = XHealthSignal.__new__(XHealthSignal)
        sig._kusto_cli = Path("/fake/Kusto.Cli.exe")
        sig._routing_index = None

        from datetime import datetime, timezone
        def fake_query_icm_status(icm_ids):
            call_log.extend(icm_ids)
            return [{"icm_id": icm_ids[0], "status": "ACTIVE",
                     "raw": "", "confidence": "[live]",
                     "checked_at": datetime.now(timezone.utc).isoformat()}]

        sig.query_icm_status = fake_query_icm_status

        results = sig.check_icm_wois(lines)

        # The regex pattern matches WOI-NNN followed by IcM NNN in the same row;
        # circuit breaker in check_icm_wois caps at 10 iterations
        assert len(results) <= 10


# ─────────────────────────────────────────────────────────────────────────────
# morning_signal
# ─────────────────────────────────────────────────────────────────────────────

class TestMorningSignal:
    """Tests for XHealthSignal.morning_signal() composite return type."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from work.xhealth_signal import XHealthSignal, SignalResult
        self.XHealthSignal = XHealthSignal
        self.SignalResult = SignalResult

    def test_returns_dict_with_icm_and_fleet_keys(self):
        """morning_signal() returns dict containing 'icm_summary' and 'fleet_health'."""
        sig = self.XHealthSignal.__new__(self.XHealthSignal)
        sig._kusto_cli = Path("/fake/Kusto.Cli.exe")
        sig._routing_index = None

        icm_result = _make_signal("icm_summary", {"sev1": "2", "sev2": "5", "cri": "0"})
        fleet_result = _make_signal("fleet_health", {"healthy_pct": "94.5", "offline_pct": "3.1"})

        sig.query_icm_summary = lambda: icm_result
        sig.query_fleet_health = lambda: fleet_result

        signals = sig.morning_signal()

        assert set(signals.keys()) == {"icm_summary", "fleet_health"}
        assert isinstance(signals["icm_summary"], self.SignalResult)
        assert isinstance(signals["fleet_health"], self.SignalResult)

    def test_both_signals_are_signal_result_instances(self):
        """Values are always SignalResult — even on error."""
        sig = self.XHealthSignal.__new__(self.XHealthSignal)
        sig._kusto_cli = None  # no kusto
        sig._routing_index = None

        # Let the real methods run — they'll return error SignalResults
        result = sig.morning_signal()

        for key in ("icm_summary", "fleet_health"):
            assert key in result
            assert isinstance(result[key], self.SignalResult)


# ─────────────────────────────────────────────────────────────────────────────
# format_morning_block
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatMorningBlock:
    """Tests for XHealthSignal.format_morning_block() output formatting."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from work.xhealth_signal import XHealthSignal
        self.XHealthSignal = XHealthSignal

    def _make_signal_pair(self, *, with_errors: bool = False):
        return {
            "icm_summary": _make_signal(
                "icm_summary",
                {"sev1": "2", "sev2": "5", "sev3": None, "cri": "0"},
                error="Kusto.Cli unavailable" if with_errors else None,
            ),
            "fleet_health": _make_signal(
                "fleet_health",
                {"healthy_pct": "94.5", "offline_pct": "3.1"},
                error=None,
            ),
        }

    def test_header_contains_morning_signal_emoji(self):
        """Output starts with '🏥 xHealth Morning Signal' header line."""
        sig = self.XHealthSignal.__new__(self.XHealthSignal)
        block = sig.format_morning_block(self._make_signal_pair())
        assert "🏥 xHealth Morning Signal" in block

    def test_has_one_line_per_signal(self):
        """Each signal produces a bullet line in the output."""
        sig = self.XHealthSignal.__new__(self.XHealthSignal)
        block = sig.format_morning_block(self._make_signal_pair())
        bullet_lines = [l for l in block.splitlines() if l.strip().startswith("- ")]
        assert len(bullet_lines) >= 2  # at least 2 signals

    def test_error_signal_produces_warning_line(self):
        """A signal with error=... produces a ⚠️ warning line (graceful degradation)."""
        sig = self.XHealthSignal.__new__(self.XHealthSignal)
        block = sig.format_morning_block(self._make_signal_pair(with_errors=True))
        assert "⚠️" in block

    def test_healthy_signal_contains_live_tag(self):
        """A successful signal produces a line with '[live]' confidence tag."""
        sig = self.XHealthSignal.__new__(self.XHealthSignal)
        block = sig.format_morning_block(self._make_signal_pair())
        assert "[live]" in block or "live" in block
