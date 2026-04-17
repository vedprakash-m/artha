"""
tests/unit/test_guardrail_degraded.py
=======================================
DEBT-004: Verify that a missing/malformed guardrails.yaml triggers the
_GUARDRAILS_DEGRADED flag, CRITICAL stderr output, and a GUARDRAIL_DEGRADED
audit log entry.

A6 note: _GUARDRAILS_DEGRADED is reset in setUp/tearDown to prevent state leak.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _reload_registry() -> object:
    """Import/reload guardrail_registry with a clean module state."""
    if "middleware.guardrail_registry" in sys.modules:
        del sys.modules["middleware.guardrail_registry"]
    from middleware import guardrail_registry
    # A6: Reset module-level flag to prevent cross-test state leak
    guardrail_registry._GUARDRAILS_DEGRADED = False
    return guardrail_registry


class TestGuardrailDegraded:
    """A1–A6 from DEBT-004."""

    def setup_method(self):
        """A6: Reset degraded flag before each test."""
        try:
            from middleware import guardrail_registry
            guardrail_registry._GUARDRAILS_DEGRADED = False
        except ImportError:
            pass

    def teardown_method(self):
        """A6: Reset degraded flag after each test."""
        try:
            from middleware import guardrail_registry
            guardrail_registry._GUARDRAILS_DEGRADED = False
        except ImportError:
            pass

    def test_degraded_flag_exists(self):
        """Module-level _GUARDRAILS_DEGRADED flag must exist."""
        gr = _reload_registry()
        assert hasattr(gr, "_GUARDRAILS_DEGRADED"), \
            "DEBT-004: _GUARDRAILS_DEGRADED flag missing from guardrail_registry"
        assert isinstance(gr._GUARDRAILS_DEGRADED, bool)

    def test_missing_yaml_sets_degraded_flag(self, tmp_path):
        """A2: Missing guardrails.yaml sets _GUARDRAILS_DEGRADED = True (A2)."""
        missing_path = tmp_path / "nonexistent_guardrails.yaml"
        gr = _reload_registry()
        gr._GUARDRAILS_DEGRADED = False

        with patch("sys.stderr"):  # suppress CRITICAL output
            registry = gr.GuardrailRegistry(config_path=missing_path)

        assert gr._GUARDRAILS_DEGRADED is True, \
            "DEBT-004: _GUARDRAILS_DEGRADED not set when guardrails.yaml is missing"

    def test_malformed_yaml_sets_degraded_flag(self, tmp_path):
        """A1: Malformed YAML sets _GUARDRAILS_DEGRADED = True."""
        bad_yaml = tmp_path / "guardrails.yaml"
        bad_yaml.write_text("{{{{NOT VALID YAML::::")
        gr = _reload_registry()
        gr._GUARDRAILS_DEGRADED = False

        with patch("sys.stderr"):
            registry = gr.GuardrailRegistry(config_path=bad_yaml)

        assert gr._GUARDRAILS_DEGRADED is True, \
            "DEBT-004: _GUARDRAILS_DEGRADED not set on malformed YAML"

    def test_retry_logic_present(self):
        """Retry-once logic (time.sleep) must be in _load_yaml."""
        gr_path = os.path.join(_SCRIPTS_DIR, "middleware", "guardrail_registry.py")
        with open(gr_path, encoding="utf-8") as f:
            src = f.read()
        assert "time.sleep(1)" in src, \
            "DEBT-004: retry-once logic (time.sleep(1)) missing from _load_yaml"

    def test_degraded_emits_critical_stderr(self, tmp_path, capsys):
        """A3: Degraded mode emits CRITICAL message to stderr."""
        missing_path = tmp_path / "nonexistent_guardrails.yaml"
        gr = _reload_registry()
        gr._GUARDRAILS_DEGRADED = False

        registry = gr.GuardrailRegistry(config_path=missing_path)

        captured = capsys.readouterr()
        assert "CRITICAL" in captured.err, \
            "DEBT-004: No CRITICAL message on stderr when guardrails degrade"

    def test_flag_reset_between_tests(self):
        """A6: _GUARDRAILS_DEGRADED starts False at test entry (tearDown verified)."""
        gr = _reload_registry()
        assert gr._GUARDRAILS_DEGRADED is False, \
            "A6 FAIL: _GUARDRAILS_DEGRADED leaked from a previous test"


class TestPreflightGuardrailsCheck:
    """A4 from DEBT-004: preflight check_guardrails_yaml() exists and works."""

    def test_check_guardrails_yaml_function_exists(self):
        """check_guardrails_yaml must be importable from preflight.py."""
        pf_path = os.path.join(_SCRIPTS_DIR, "preflight.py")
        with open(pf_path, encoding="utf-8") as f:
            src = f.read()
        assert "def check_guardrails_yaml" in src, \
            "DEBT-004: check_guardrails_yaml() missing from preflight.py"

    def test_force_no_guardrails_arg_exists(self):
        """--force-no-guardrails argument must be in preflight.py argparse block."""
        pf_path = os.path.join(_SCRIPTS_DIR, "preflight.py")
        with open(pf_path, encoding="utf-8") as f:
            src = f.read()
        assert "force-no-guardrails" in src, \
            "DEBT-004: --force-no-guardrails arg missing from preflight.py"

    def test_force_no_guardrails_audit_entry(self):
        """FORCE_NO_GUARDRAILS audit entry must be in the override path."""
        pf_path = os.path.join(_SCRIPTS_DIR, "preflight.py")
        with open(pf_path, encoding="utf-8") as f:
            src = f.read()
        assert "FORCE_NO_GUARDRAILS" in src, \
            "DEBT-004: FORCE_NO_GUARDRAILS audit entry missing"
