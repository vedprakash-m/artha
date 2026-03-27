"""tests/unit/test_pii_inprocess.py — T7-7..10: in-process PII guard tests for action_executor."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_ARTHA_ROOT  = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
for _p in [str(_ARTHA_ROOT), str(_SCRIPTS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from action_executor import _pii_scan_params  # type: ignore[import]


# ---------------------------------------------------------------------------
# T7-7: in-process pii_guard.scan called from action_executor
# ---------------------------------------------------------------------------

def test_t7_7_pii_scan_uses_inprocess_scan():
    """_pii_scan_params calls pii_guard.scan() in-process (no subprocess)."""
    # Patch pii_guard.scan directly so we can verify it's called
    mock_scan = MagicMock(return_value=(False, {}))  # No PII found

    with patch.dict("sys.modules", {"pii_guard": MagicMock(scan=mock_scan)}):
        clean, findings = _pii_scan_params(
            {"message": "Hello world — no PII here"},
            pii_allowlist=[],
        )

    assert clean is True
    assert findings == []
    mock_scan.assert_called()


# ---------------------------------------------------------------------------
# T7-8: in-process PII check catches PII in action parameters
# ---------------------------------------------------------------------------

def test_t7_8_pii_scan_catches_pii():
    """_pii_scan_params returns (False, findings) when PII is detected."""
    mock_scan = MagicMock(return_value=(True, {"SSN": 1}))  # PII found

    with patch.dict("sys.modules", {"pii_guard": MagicMock(scan=mock_scan)}):
        clean, findings = _pii_scan_params(
            {"subject": "My SSN is 123-45-6789"},
            pii_allowlist=[],
        )

    assert clean is False
    assert len(findings) > 0
    assert "subject" in findings[0]


# ---------------------------------------------------------------------------
# T7-9: in-process PII check passes clean text
# ---------------------------------------------------------------------------

def test_t7_9_pii_scan_passes_clean_text():
    """_pii_scan_params returns (True, []) for text without PII."""
    mock_scan = MagicMock(return_value=(False, {}))

    with patch.dict("sys.modules", {"pii_guard": MagicMock(scan=mock_scan)}):
        clean, findings = _pii_scan_params(
            {"body": "Please schedule the meeting for 3pm"},
            pii_allowlist=[],
        )

    assert clean is True
    assert findings == []


# ---------------------------------------------------------------------------
# T7-10: filter_text handles empty string without error
# ---------------------------------------------------------------------------

def test_t7_10_filter_text_handles_empty_string():
    """pii_guard.filter_text('') does not raise and returns an empty string."""
    from pii_guard import filter_text  # type: ignore[import]

    result, found_types = filter_text("")
    assert isinstance(result, str)
    assert result == ""
    assert isinstance(found_types, dict)
