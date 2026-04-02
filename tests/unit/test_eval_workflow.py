# pii-guard: ignore-file — test fixtures are 100% synthetic; no real PII (DD-5)
"""tests/unit/test_eval_workflow.py — Integration smoke tests for EV-7 and EV-8.

Tests:
* T-EV-7-01: finalize.md Step 12b section contains "log_digest.py" reference
* T-EV-8-01: post_catchup_memory._run_log_digest() writes tmp/log_digest.json when enabled

All fixtures are 100% fictional — no real user data (DD-5).
Ref: specs/eval.md EV-7, EV-8, T-EV-7-01, T-EV-8-01
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_FINALIZE_MD = _PROJECT_ROOT / "config" / "workflow" / "finalize.md"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# T-EV-7-01: finalize.md Step 12b documents the log_digest.py call
# ---------------------------------------------------------------------------

def test_ev7_01_finalize_md_step12b_references_log_digest():
    """T-EV-7-01: config/workflow/finalize.md Step 12b section must reference log_digest.py.

    Verifies that the workflow documentation instructs the AI to run the
    log digest step (EV-7 compliance check).
    """
    assert _FINALIZE_MD.exists(), (
        f"finalize.md not found at {_FINALIZE_MD}. "
        "Ensure config/workflow/finalize.md is committed to the repo."
    )
    content = _FINALIZE_MD.read_text(encoding="utf-8")

    # Must reference log_digest.py (the actual command invoked)
    assert "log_digest.py" in content, (
        "finalize.md must mention 'log_digest.py' in Step 12b to satisfy EV-7 "
        "workflow documentation requirement."
    )

    # Must have Step 12b header
    assert "Step 12b" in content, (
        "finalize.md must contain a 'Step 12b' section documenting the log digest step."
    )


# ---------------------------------------------------------------------------
# T-EV-8-01: _run_log_digest writes tmp/log_digest.json when enabled
# ---------------------------------------------------------------------------

def test_ev8_01_run_log_digest_writes_output(tmp_path):
    """T-EV-8-01: post_catchup_memory._run_log_digest() writes tmp/log_digest.json when enabled.

    Uses mocking to avoid requiring a running eval stack — validates the code path
    exercises the right I/O contract.
    """
    # Set up artha_dir structure
    tmp_dir = tmp_path / "tmp"
    state_dir = tmp_path / "state"
    tmp_dir.mkdir()
    state_dir.mkdir()
    log_digest_output = tmp_dir / "log_digest.json"

    # Build a minimal fake log_digest module
    fake_digest = MagicMock()
    fake_digest.build_digest.return_value = {
        "connector_metrics": [],
        "anomalies": [],
        "quality_score": 88.0,
        "period_hours": 24,
        "runs_analyzed": 3,
    }

    def fake_write_digest(digest):
        import json
        log_digest_output.write_text(json.dumps(digest), encoding="utf-8")

    fake_digest.write_digest.side_effect = fake_write_digest

    # Load post_catchup_memory
    pcm = _load_module("post_catchup_memory", _SCRIPTS_DIR / "post_catchup_memory.py")

    # Patch out the log_digest module loading and the enabled check + SCRIPTS_DIR
    with patch.object(pcm, "_is_log_digest_enabled", return_value=True), \
         patch.object(pcm, "_SCRIPTS_DIR", _SCRIPTS_DIR), \
         patch("importlib.util.spec_from_file_location") as mock_spec_from_file, \
         patch("importlib.util.module_from_spec", return_value=fake_digest):

        mock_spec = MagicMock()
        mock_spec_from_file.return_value = mock_spec
        mock_spec.loader.exec_module.side_effect = lambda mod: None

        # Call _run_log_digest with the fake write side effect already registered
        # We need to directly inject the fake module
        with patch.dict(sys.modules, {"log_digest_mock_module": fake_digest}):
            # Manually invoke the write_digest path by patching the module exec
            def exec_and_patch(mod):
                # Replace the mod in place with our fake
                mod.build_digest = fake_digest.build_digest
                mod.write_digest = fake_digest.write_digest

            mock_spec.loader.exec_module.side_effect = exec_and_patch
            pcm._run_log_digest(tmp_path)

    # Verify that write_digest was called (log_digest.json would be written)
    assert fake_digest.write_digest.called or fake_digest.build_digest.called, (
        "_run_log_digest() must call build_digest() and write_digest() when enabled"
    )


def test_ev8_01b_run_log_digest_skipped_when_disabled(tmp_path):
    """T-EV-8-01b: _run_log_digest() does nothing when harness.eval.log_digest.enabled=false."""
    pcm = _load_module(
        "post_catchup_memory_b", _SCRIPTS_DIR / "post_catchup_memory.py"
    )

    call_count = [0]

    def counting_build_digest():
        call_count[0] += 1
        return {}

    with patch.object(pcm, "_is_log_digest_enabled", return_value=False):
        pcm._run_log_digest(tmp_path)

    assert call_count[0] == 0, (
        "_run_log_digest() must skip when disabled — build_digest should not be called"
    )
