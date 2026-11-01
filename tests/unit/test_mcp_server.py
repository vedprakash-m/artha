"""Unit tests for scripts/mcp_server.py — approval gating, domain validation, audit."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# We need to mock FastMCP before importing mcp_server, because the import
# instantiates the mcp object at module level.
sys.modules.setdefault(
    "mcp", MagicMock()
)
sys.modules.setdefault(
    "mcp.server", MagicMock()
)
sys.modules.setdefault(
    "mcp.server.fastmcp", MagicMock(FastMCP=MagicMock())
)


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
