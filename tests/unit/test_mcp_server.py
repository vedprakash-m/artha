"""Unit tests for scripts/mcp_server.py — approval gating, domain validation, audit."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"

class _IdentityDecoratorFastMCP:
    def __init__(self, *_args, **_kwargs):
        pass

    def tool(self):
        return lambda function: function


@pytest.fixture(scope="module", autouse=True)
def _stub_fastmcp():
    """Stub mcp packages only for this module and restore sys.modules afterward."""
    fastmcp_mod = MagicMock(FastMCP=_IdentityDecoratorFastMCP)
    stubs = {
        "mcp": MagicMock(),
        "mcp.server": MagicMock(),
        "mcp.server.fastmcp": fastmcp_mod,
    }
    saved = {k: sys.modules.get(k) for k in (*stubs, "mcp_server")}
    for k, v in stubs.items():
        if k not in sys.modules:
            sys.modules[k] = v
    prev_fastmcp = getattr(sys.modules["mcp.server.fastmcp"], "FastMCP", None)
    sys.modules["mcp.server.fastmcp"].FastMCP = _IdentityDecoratorFastMCP
    try:
        yield
    finally:
        if prev_fastmcp is not None:
            sys.modules["mcp.server.fastmcp"].FastMCP = prev_fastmcp
        for k, orig in saved.items():
            if orig is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = orig


# ── _require_approval ────────────────────────────────────────────────────────

class TestRequireApproval:
    @pytest.fixture(autouse=True)
    def _import(self):
        import mcp_server
        self.mod = mcp_server

    def test_rejected_when_not_approved(self):
        with patch.object(self.mod, "_audit"):
            result = self.mod._require_approval(False, "artha_write_state", {"domain": "goals"})
        assert result is not None
        data = json.loads(result)
        assert data["error"] == "Write operation requires approved=True."

    def test_passes_when_approved(self):
        result = self.mod._require_approval(True, "artha_write_state", {"domain": "goals"})
        assert result is None


# ── _known_domains ───────────────────────────────────────────────────────────

class TestKnownDomains:
    def test_returns_domains_from_state_dir(self, tmp_path):
        import mcp_server

        (tmp_path / "goals.md").write_text("# Goals")
        (tmp_path / "finance.md.age").write_bytes(b"encrypted")
        (tmp_path / "templates").mkdir()

        with patch.object(mcp_server, "_STATE_DIR", tmp_path):
            domains = mcp_server._known_domains()

        assert "goals" in domains
        assert "finance" in domains
        assert "templates" not in domains


# ── _read_state_file ─────────────────────────────────────────────────────────

class TestReadStateFile:
    def test_reads_plain_md(self, tmp_path):
        import mcp_server

        (tmp_path / "goals.md").write_text("# Goals\n- item 1")
        with patch.object(mcp_server, "_STATE_DIR", tmp_path):
            content = mcp_server._read_state_file("goals")
        assert "# Goals" in content

    def test_strips_md_suffix(self, tmp_path):
        import mcp_server

        (tmp_path / "goals.md").write_text("# Goals")
        with patch.object(mcp_server, "_STATE_DIR", tmp_path):
            content = mcp_server._read_state_file("goals.md")
        assert "# Goals" in content

    def test_raises_on_missing(self, tmp_path):
        import mcp_server

        with patch.object(mcp_server, "_STATE_DIR", tmp_path):
            with pytest.raises(FileNotFoundError, match="nonexistent"):
                mcp_server._read_state_file("nonexistent")


# ── _write_state_file ────────────────────────────────────────────────────────

class TestWriteStateFile:
    def test_writes_plain_for_non_encrypted_domain(self, tmp_path):
        import mcp_server

        with patch.object(mcp_server, "_STATE_DIR", tmp_path):
            encrypted = mcp_server._write_state_file("goals", "# Goals\n- updated")
        assert encrypted is False
        assert (tmp_path / "goals.md").read_text() == "# Goals\n- updated"

    def test_encrypts_sensitive_domain(self, tmp_path):
        import mcp_server

        mock_vault = MagicMock()
        mock_vault.get_public_key.return_value = "age1key..."
        mock_vault.age_encrypt.return_value = True

        with patch.object(mcp_server, "_STATE_DIR", tmp_path), \
             patch.dict(sys.modules, {"vault": mock_vault}):
            encrypted = mcp_server._write_state_file("finance", "# Finance")

        assert encrypted is True
        mock_vault.age_encrypt.assert_called_once()


# ── _audit ───────────────────────────────────────────────────────────────────

class TestAudit:
    def test_scrubs_sensitive_keys(self, tmp_path):
        import mcp_server

        audit_log = tmp_path / "audit.md"
        audit_log.write_text("")
        with patch.object(mcp_server, "_AUDIT_LOG", audit_log):
            mcp_server._audit(
                "artha_send_email",
                {"to": "a@b.com", "token": "secret123", "content": "body text"},
                "success",
            )
        text = audit_log.read_text()
        assert "secret123" not in text
        assert "body text" not in text
        assert "artha_send_email" in text


class TestPersonalReadBoundary:
    def test_fetch_and_state_reads_fail_closed_before_helpers(self):
        import mcp_server

        with patch.object(mcp_server, "_fetch_in_memory", side_effect=AssertionError), \
             patch.object(mcp_server, "_read_state_file", side_effect=AssertionError), \
             patch.object(mcp_server, "_check_rate_limit", side_effect=AssertionError):
            assert json.loads(mcp_server.artha_fetch_data("2026-10-01")) == {
                "error": "personal source classification unavailable"}
            assert json.loads(mcp_server.artha_read_state("goals")) == {
                "error": "personal State classification unavailable"}

    def test_state_write_fails_closed_before_read_or_write_helpers(self):
        import mcp_server

        with patch.object(mcp_server, "_known_domains", side_effect=AssertionError), \
             patch.object(mcp_server, "_read_state_file", side_effect=AssertionError), \
             patch.object(mcp_server, "_write_state_file", side_effect=AssertionError):
            assert json.loads(mcp_server.artha_write_state("goals", "# synthetic", approved=True)) == {
                "error": "personal State classification unavailable"}

    def test_core_mcp_oversize_and_exception_results_are_fixed(self):
        from catchup_core import mcp as core_mcp

        with patch.object(core_mcp, "MAX_RESULT_CHARS", 20):
            oversized = core_mcp._text_result({"payload": "SYNTHETIC-CANARY" * 10})
        assert oversized["isError"] is True
        assert "SYNTHETIC-CANARY" not in oversized["content"][0]["text"]

        result = core_mcp.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "beliefs", "arguments": {}}},
            lambda *_args: (_ for _ in ()).throw(RuntimeError("SYNTHETIC-EXCEPTION-CANARY")))
        assert "SYNTHETIC-EXCEPTION-CANARY" not in result["result"]["content"][0]["text"]
        assert result["result"]["content"][0]["text"] == '{"error": "the request could not be completed"}'

        invalid = core_mcp.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "beliefs", "arguments": "SYNTHETIC-CANARY"}},
            lambda *_args: pytest.fail("invalid arguments reached the handler"))
        assert invalid["result"]["content"][0]["text"] == '{"error": "invalid arguments"}'
