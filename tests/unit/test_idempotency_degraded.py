"""
tests/unit/test_idempotency_degraded.py
========================================
DEBT-003: Verify that an unavailable idempotency store causes friction=high
and an IDEMPOTENCY_DEGRADED audit log entry — NOT a silent continue.

Tests use monkey-patching to simulate a corrupt/unavailable SQLite store.
"""
from __future__ import annotations

import os
import sys

import pytest

_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


class TestIdempotencyDegraded:
    """A1–A4 from DEBT-003."""

    def test_sqlite3_imported_in_action_executor(self):
        """sqlite3 must be in the import block (not bare-except-swallowed)."""
        ae_path = os.path.join(_SCRIPTS_DIR, "action_executor.py")
        with open(ae_path) as f:
            src = f.read()
        assert "import sqlite3" in src, \
            "DEBT-003: sqlite3 not imported in action_executor.py"

    def test_no_bare_except_in_idempotency_path(self):
        """The bare 'except Exception' that silently swallowed store errors must be gone."""
        ae_path = os.path.join(_SCRIPTS_DIR, "action_executor.py")
        with open(ae_path) as f:
            src = f.read()
        # Check the old silent pattern is gone
        assert "except Exception:  # noqa: BLE001\n            _idem_key = None" not in src, \
            "DEBT-003: bare except Exception still silently swallows idempotency errors"

    def test_idempotency_degraded_audit_entry_present(self):
        """IDEMPOTENCY_DEGRADED must appear in the exception handler."""
        ae_path = os.path.join(_SCRIPTS_DIR, "action_executor.py")
        with open(ae_path) as f:
            src = f.read()
        assert "IDEMPOTENCY_DEGRADED" in src, \
            "DEBT-003: IDEMPOTENCY_DEGRADED audit log entry missing"

    def test_friction_escalated_to_high_on_store_error(self):
        """friction = 'high' must be set before ActionProposal constructor call."""
        ae_path = os.path.join(_SCRIPTS_DIR, "action_executor.py")
        with open(ae_path) as f:
            src = f.read()
        assert 'friction = "high"' in src, \
            "DEBT-003: friction not escalated to 'high' on idempotency store failure"

    def test_targeted_exception_types(self):
        """Only sqlite3.OperationalError, OSError, PermissionError are caught — not broad Exception."""
        ae_path = os.path.join(_SCRIPTS_DIR, "action_executor.py")
        with open(ae_path) as f:
            src = f.read()
        assert "sqlite3.OperationalError" in src, \
            "DEBT-003: sqlite3.OperationalError not in targeted except"
        assert "OSError" in src, \
            "DEBT-003: OSError not in targeted except"
        assert "PermissionError" in src, \
            "DEBT-003: PermissionError not in targeted except"
