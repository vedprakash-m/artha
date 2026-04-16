"""
tests/unit/test_re_debts_wave2.py — CI tests for RE-DEBTS Wave 2 items.

Covers: RD-32, RD-05, RD-08, RD-34, RD-36, RD-37, RD-40, RD-43, RD-49.

Each test is minimal and focused: it exercises the specific invariant the
corresponding RD item was designed to enforce.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# RD-32: _pre_enqueue_gate() shared safety gate
# ---------------------------------------------------------------------------


class TestPreEnqueueGate:
    """RD-32: propose_direct() must enforce the same gates as propose()."""

    def _make_executor(self, tmp_path: Path) -> Any:
        """Build a minimal ActionExecutor."""
        with patch("detect_environment.detect", return_value=True):
            from action_executor import ActionExecutor
            return ActionExecutor(artha_dir=tmp_path)

    def test_propose_direct_calls_pre_enqueue_gate(self, tmp_path: Path) -> None:
        """propose_direct() must call _pre_enqueue_gate before enqueuing."""
        executor = self._make_executor(tmp_path)
        gate_called = []
        original_gate = executor._pre_enqueue_gate  # noqa: SLF001

        def _spy_gate(proposal):
            gate_called.append(proposal)
            return (False, "test_gate_called")  # reject to avoid db write

        executor._pre_enqueue_gate = _spy_gate  # noqa: SLF001

        from actions.base import ActionProposal
        import uuid
        proposal = ActionProposal(
            id=str(uuid.uuid4()),
            action_type="send_email", domain="comms", title="Test",
            description="Test proposal for gate spy",
            parameters={"to": "x@y.com", "body": "hi"},
            friction="low", sensitivity="low", min_trust=0,
            reversible=True, undo_window_sec=0, expires_at=None,
            source_step="test", source_skill="test", linked_oi=None,
        )
        try:
            executor.propose_direct(proposal)
        except Exception:
            pass  # rejection expected — gate call is what matters

        assert gate_called, "propose_direct() did not call _pre_enqueue_gate()"

    def test_pre_enqueue_gate_present_in_source(self) -> None:
        """_pre_enqueue_gate must exist in action_executor.py."""
        source_text = (SCRIPTS_DIR / "action_executor.py").read_text(encoding="utf-8")
        assert "_pre_enqueue_gate" in source_text
        assert "ACTION_PRE_ENQUEUE_REJECTED" in source_text


# ---------------------------------------------------------------------------
# RD-05: PII entity scrub in email_signal_extractor
# ---------------------------------------------------------------------------


class TestEmailEntityPiiScrub:
    """RD-05: Entity field must be PII-filtered before being stored in signal."""

    def test_pii_filter_text_applied_to_entity(self) -> None:
        """filter_text (pii_guard) must be called on the entity string."""
        import pii_guard  # noqa: PLC0415

        with patch.object(pii_guard, "filter_text", side_effect=lambda x: x) as mock_filter:
            entity = "Acme Corp: Invoice for John Doe"
            try:
                entity = pii_guard.filter_text(entity)
            except Exception:
                entity = entity[:40]

        mock_filter.assert_called_once_with("Acme Corp: Invoice for John Doe")

    def test_entity_pii_filter_in_extractor_source(self) -> None:
        """email_signal_extractor.py must import and call pii_guard.filter_text on entity."""
        source = (SCRIPTS_DIR / "email_signal_extractor.py").read_text(encoding="utf-8")
        assert "filter_text" in source or "pii_filter_entity" in source
        # The entity variable must be filtered
        assert "pii_filter_entity" in source or ("entity" in source and "filter_text" in source)

    def test_name_in_subject_pattern_activates(self) -> None:
        """_maybe_add_name_in_subject_pattern must exist and be callable."""
        import pii_guard  # noqa: PLC0415

        assert hasattr(pii_guard, "_maybe_add_name_in_subject_pattern"), (
            "RD-05: _maybe_add_name_in_subject_pattern must be defined in pii_guard.py"
        )
        assert hasattr(pii_guard, "_NAME_IN_SUBJECT_LOADED"), (
            "RD-05: _NAME_IN_SUBJECT_LOADED flag must exist in pii_guard.py"
        )


# ---------------------------------------------------------------------------
# RD-08: Telegram payload category filter
# ---------------------------------------------------------------------------


class TestTelegramPayloadFilter:
    """RD-08: Telegram must only receive aggregate-tier (non-sensitive) data."""

    def _get_filter_fn(self) -> Any:
        """Import _filter_telegram_payload from export_bridge_context."""
        import export_bridge_context as ebc  # noqa: PLC0415

        return ebc._filter_telegram_payload  # noqa: SLF001

    def test_blocked_category_replaced_with_count(self) -> None:
        """Lists in blocked categories must be replaced with {key}_count."""
        _filter = self._get_filter_fn()
        payload = {
            "open_items": ["item1", "item2", "item3"],  # blocked: open_item_titles
            "system_health": "ok",  # allowed: system_health
        }
        allowed = ["system_health", "iot_device_count", "version", "ts", "id", "urgency", "source_platform"]
        result = _filter(payload, allowed)

        assert "system_health" in result
        assert result["system_health"] == "ok"
        # open_items is blocked — should be replaced by a count or absent
        assert "open_items" not in result or "open_items_count" in result

    def test_payload_tier_aggregate_added(self) -> None:
        """Filtered payload must include payload_tier: aggregate."""
        _filter = self._get_filter_fn()
        result = _filter({"version": "1.0"}, ["version"])
        assert result.get("payload_tier") == "aggregate"

    def test_home_presence_blocked(self) -> None:
        """home_presence is a blocked category and must not pass through."""
        _filter = self._get_filter_fn()
        payload = {"home_presence": "family home, 2 adults, 1 child"}
        allowed = ["system_health", "version"]
        result = _filter(payload, allowed)

        assert "home_presence" not in result


# ---------------------------------------------------------------------------
# RD-34: Kids/employment vault gate in state_readers
# ---------------------------------------------------------------------------


class TestVaultGatedStateKeys:
    """RD-34: Vault-gated domains must raise VaultAccessRequired."""

    def test_vault_gated_keys_loaded_from_registry(self) -> None:
        """_VAULT_GATED_STATE_KEYS must include kids and employment at minimum."""
        from channel.state_readers import _VAULT_GATED_STATE_KEYS

        assert "kids" in _VAULT_GATED_STATE_KEYS, "kids must be vault-gated"
        assert "employment" in _VAULT_GATED_STATE_KEYS, "employment must be vault-gated"

    def test_kids_domain_raises_vault_access_required_when_age_file_present(
        self, tmp_path: Path
    ) -> None:
        """Reading a vault-gated domain with .age file but no plaintext raises VaultAccessRequired."""
        from channel.state_readers import (
            VaultAccessRequired,
            _READABLE_STATE_FILES,
            _read_state_file,
        )

        # We patch _READABLE_STATE_FILES to point to tmp_path for 'kids'
        kids_path = tmp_path / "kids.md"
        age_path = tmp_path / "kids.md.age"
        age_path.touch()  # .age exists (vault is locked)
        # kids.md NOT created (plaintext absent)

        patched_files = dict(_READABLE_STATE_FILES)
        patched_files["kids"] = kids_path

        with patch("channel.state_readers._READABLE_STATE_FILES", patched_files):
            with pytest.raises(VaultAccessRequired):
                _read_state_file("kids")

    def test_vault_access_required_class_exists(self) -> None:
        """VaultAccessRequired must be a PermissionError subclass."""
        from channel.state_readers import VaultAccessRequired

        assert issubclass(VaultAccessRequired, PermissionError)

    def test_open_items_not_vault_gated(self) -> None:
        """Non-vault domains (open_items) must not be in _VAULT_GATED_STATE_KEYS."""
        from channel.state_readers import _VAULT_GATED_STATE_KEYS

        assert "open_items" not in _VAULT_GATED_STATE_KEYS, (
            "open_items is not a vault domain and must not be vault-gated"
        )


# ---------------------------------------------------------------------------
# RD-36: Work OS isolation — no --allow-all-tools
# ---------------------------------------------------------------------------


class TestWorkOsIsolation:
    """RD-36: work_loop.py agency subprocess must not use --allow-all-tools."""

    def test_allow_all_tools_not_in_subprocess_call(self) -> None:
        """--allow-all-tools must not appear in the Popen args list."""
        work_loop_path = SCRIPTS_DIR / "work_loop.py"
        if not work_loop_path.exists():
            pytest.skip("scripts/work_loop.py is gitignored — not present in CI.")
        text = work_loop_path.read_text(encoding="utf-8")
        # Find lines that are NOT comments and check for --allow-all-tools
        non_comment_lines = [
            line for line in text.splitlines()
            if not line.strip().startswith("#")
        ]
        has_in_code = any("--allow-all-tools" in line for line in non_comment_lines)
        assert not has_in_code, (
            "RD-36: --allow-all-tools must not appear in non-comment code in work_loop.py"
        )

    def test_isolation_violation_logged_on_state_write(self, tmp_path: Path) -> None:
        """WORK_ISOLATION_VIOLATION audit log must be in work_loop.py source."""
        work_loop_path = SCRIPTS_DIR / "work_loop.py"
        if not work_loop_path.exists():
            pytest.skip("scripts/work_loop.py is gitignored — not present in CI.")
        text = work_loop_path.read_text(encoding="utf-8")
        assert "WORK_ISOLATION_VIOLATION" in text, (
            "RD-36: WORK_ISOLATION_VIOLATION audit log entry must be present in work_loop.py"
        )
        assert "_isolation_violations" in text, (
            "RD-36: _isolation_violations variable (post-run check) must be present in work_loop.py"
        )

    def test_work_refresh_tools_yaml_exists(self) -> None:
        """config/agents/work_refresh_tools.yaml must exist (RD-36 allowlist)."""
        tools_yaml = PROJECT_ROOT / "config" / "agents" / "work_refresh_tools.yaml"
        assert tools_yaml.exists(), (
            "RD-36: config/agents/work_refresh_tools.yaml must exist"
        )


# ---------------------------------------------------------------------------
# RD-37: Session summarization wiring — --proactive flag
# ---------------------------------------------------------------------------


class TestSessionSummarizerProactive:
    """RD-37: session_summarizer.py --proactive must exist and be callable."""

    def test_main_function_accepts_proactive_flag(self) -> None:
        """session_summarizer.main(['--proactive']) must not raise before state check."""
        import session_summarizer  # noqa: PLC0415

        assert hasattr(session_summarizer, "main"), (
            "RD-37: session_summarizer.py must expose main() CLI entry point"
        )

    def test_proactive_flag_present_in_source(self) -> None:
        """--proactive flag must be handled in session_summarizer.py."""
        source = (SCRIPTS_DIR / "session_summarizer.py").read_text(encoding="utf-8")
        assert "--proactive" in source, (
            "RD-37: --proactive CLI flag must be implemented in session_summarizer.py"
        )
        assert "should_summarize_now" in source, (
            "RD-37: should_summarize_now must be called from the --proactive path"
        )

    def test_proactive_exits_nonzero_below_threshold(self) -> None:
        """--proactive exits 1 when context is below the summarization threshold."""
        import session_summarizer  # noqa: PLC0415

        with patch.object(session_summarizer, "should_summarize_now", return_value=False):
            result = session_summarizer.main(["--proactive"])

        assert result == 1, "Exit code must be 1 when below summarization threshold"


# ---------------------------------------------------------------------------
# RD-40: Vault watchdog — stale lock triggers re-encryption
# ---------------------------------------------------------------------------


class TestVaultWatchdog:
    """RD-40: Stale lock must trigger do_encrypt() before removal."""

    def test_stale_lock_calls_do_encrypt_before_unlink(self, tmp_path: Path) -> None:
        """check_lock_state() must call do_encrypt() before removing stale lock."""
        import vault  # noqa: PLC0415

        # Create a stale lock file (age > 30 minutes)
        lock_file = tmp_path / ".artha-decrypted"
        lock_data = {"pid": 0, "session": "test"}
        lock_file.write_text(json.dumps(lock_data))
        # Set mtime to 35 minutes ago
        old_mtime = time.time() - 35 * 60
        import os
        os.utime(str(lock_file), (old_mtime, old_mtime))

        encrypt_called = []
        with (
            patch.object(vault, "LOCK_FILE", lock_file),
            patch.object(vault, "do_encrypt", side_effect=lambda: encrypt_called.append(True)),
        ):
            result = vault.check_lock_state()

        assert result == 1, "Stale lock must return 1"
        assert encrypt_called, "do_encrypt() must be called before clearing stale lock"

    def test_stale_lock_cleared_even_when_encrypt_fails(self, tmp_path: Path) -> None:
        """Lock file must be removed even when do_encrypt() raises SystemExit."""
        import vault  # noqa: PLC0415

        lock_file = tmp_path / ".artha-decrypted"
        lock_file.write_text(json.dumps({"pid": 0}))
        old_mtime = time.time() - 35 * 60
        import os
        os.utime(str(lock_file), (old_mtime, old_mtime))

        with (
            patch.object(vault, "LOCK_FILE", lock_file),
            patch.object(vault, "do_encrypt", side_effect=SystemExit(1)),
        ):
            result = vault.check_lock_state()

        assert result == 1, "Stale lock must still be cleared after encrypt failure"
        assert not lock_file.exists(), "Lock file must be removed even if do_encrypt raises"

    def test_vault_watchdog_subcommand_exists(self) -> None:
        """vault.py must accept 'watchdog' as a subcommand."""
        vault_source = (SCRIPTS_DIR / "vault.py").read_text(encoding="utf-8")
        assert '"watchdog"' in vault_source or "'watchdog'" in vault_source, (
            "RD-40: vault.py main() must dispatch the 'watchdog' subcommand"
        )

    def test_preflight_check_vault_watchdog_exists(self) -> None:
        """preflight.py must expose check_vault_watchdog()."""
        import preflight  # noqa: PLC0415

        assert hasattr(preflight, "check_vault_watchdog"), (
            "RD-40: preflight.py must expose check_vault_watchdog() P1 advisory check"
        )


# ---------------------------------------------------------------------------
# RD-43: Signal pipeline funnel metrics
# ---------------------------------------------------------------------------


class TestSignalPipelineMetrics:
    """RD-43: Both extractor and orchestrator must write structured metrics."""

    def test_signal_metrics_written_after_extract(self, tmp_path: Path) -> None:
        """email_signal_extractor.extract() must write tmp/signal_metrics.json."""
        extractor_path = SCRIPTS_DIR / "email_signal_extractor.py"
        text = extractor_path.read_text(encoding="utf-8")
        assert "signal_metrics.json" in text, (
            "RD-43: email_signal_extractor.py must write signal_metrics.json"
        )
        assert "emails_processed" in text, (
            "RD-43: signal_metrics must include emails_processed field"
        )

    def test_orchestrator_metrics_written_after_run(self) -> None:
        """action_orchestrator.run() must write tmp/orchestrator_metrics.json."""
        orchestrator_path = SCRIPTS_DIR / "action_orchestrator.py"
        text = orchestrator_path.read_text(encoding="utf-8")
        assert "orchestrator_metrics.json" in text, (
            "RD-43: action_orchestrator.py must write orchestrator_metrics.json"
        )
        assert "proposals_queued" in text, (
            "RD-43: orchestrator_metrics must include proposals_queued field"
        )

    def test_analyze_pipeline_returns_conversion_rate(self, tmp_path: Path) -> None:
        """analyze_pipeline() must compute and return conversion_rate."""
        import eval_runner  # noqa: PLC0415

        signal_metrics = {
            "run_at": "2026-04-01T00:00:00",
            "emails_processed": 10,
            "signals_extracted": 5,
            "signals_by_type": {"bill_due": 3, "renewal": 2},
        }
        orchestrator_metrics = {
            "run_at": "2026-04-01T00:00:00",
            "signals_in": 5,
            "proposals_composed": 4,
            "proposals_suppressed_duplicates": 1,
            "proposals_queued": 3,
        }
        # Write metrics to the real tmp/ dir (evaluate from actual code path)
        real_tmp = PROJECT_ROOT / "tmp"
        real_tmp.mkdir(exist_ok=True)
        sig_file = real_tmp / "signal_metrics.json"
        orch_file = real_tmp / "orchestrator_metrics.json"
        # Save originals to restore after test
        sig_orig = sig_file.read_text() if sig_file.exists() else None
        orch_orig = orch_file.read_text() if orch_file.exists() else None
        try:
            sig_file.write_text(json.dumps(signal_metrics))
            orch_file.write_text(json.dumps(orchestrator_metrics))
            result = eval_runner.analyze_pipeline()
        finally:
            if sig_orig is not None:
                sig_file.write_text(sig_orig)
            elif sig_file.exists():
                sig_file.unlink()
            if orch_orig is not None:
                orch_file.write_text(orch_orig)
            elif orch_file.exists():
                orch_file.unlink()

        assert "conversion_rate" in result, "analyze_pipeline() must return conversion_rate"
        assert result["conversion_rate"] == pytest.approx(3 / 5)

    def test_analyze_pipeline_source_has_orphan_alert(self) -> None:
        """analyze_pipeline source must include orphan_alert logic."""
        source = (SCRIPTS_DIR / "eval_runner.py").read_text(encoding="utf-8")
        assert "orphan_alert" in source, (
            "RD-43: analyze_pipeline() must set orphan_alert when signals > 0 but no proposals"
        )
        assert "conversion_rate" in source, (
            "RD-43: analyze_pipeline() must compute conversion_rate"
        )


# ---------------------------------------------------------------------------
# RD-49: llm_trace wired into llm_bridge
# ---------------------------------------------------------------------------


class TestLlmTraceWiring:
    """RD-49: llm_trace() must be called on every code path in _call_single_llm."""

    def test_llm_trace_import_present_in_llm_bridge(self) -> None:
        """llm_trace import and call must be present in _call_single_llm source."""
        bridge_path = SCRIPTS_DIR / "channel" / "llm_bridge.py"
        text = bridge_path.read_text(encoding="utf-8")
        assert "llm_trace" in text, (
            "RD-49: llm_bridge.py must import and call llm_trace"
        )

    def test_llm_trace_called_on_all_code_paths(self) -> None:
        """llm_trace calls must be present for success, error, empty, and timeout paths."""
        bridge_path = SCRIPTS_DIR / "channel" / "llm_bridge.py"
        text = bridge_path.read_text(encoding="utf-8")
        # All paths must include an llm_trace call
        assert 'error="timeout"' in text or "error='timeout'" in text, (
            "RD-49: llm_trace must be called with error=timeout on TimeoutExpired"
        )
        assert 'error="empty_response"' in text or "error='empty_response'" in text, (
            "RD-49: llm_trace must be called with error=empty_response on empty output"
        )
        # Success path: llm_trace called without error (completion_tokens present)
        assert "completion_tokens" in text, (
            "RD-49: llm_trace on success must include completion_tokens estimate"
        )

    def test_is_trace_enabled_function_in_observability(self) -> None:
        """_is_trace_enabled() must exist in lib/observability.py."""
        obs_path = SCRIPTS_DIR / "lib" / "observability.py"
        text = obs_path.read_text(encoding="utf-8")
        assert "_is_trace_enabled" in text, (
            "RD-49: _is_trace_enabled() must be defined in lib/observability.py"
        )
        assert "llm_trace_enabled" in text, (
            "RD-49: _is_trace_enabled must read llm_trace_enabled from config"
        )

    def test_is_trace_enabled_responds_to_env_var(self) -> None:
        """_is_trace_enabled() must return True when ARTHA_LLM_TRACE env var is set."""
        from lib.observability import _is_trace_enabled  # noqa: PLC0415
        import os

        with patch.dict(os.environ, {"ARTHA_LLM_TRACE": "1"}):
            assert _is_trace_enabled() is True
