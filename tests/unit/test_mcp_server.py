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
        self.registered_tools: set[str] = set()

    def tool(self):
        def register(function):
            self.registered_tools.add(function.__name__)
            return function
        return register


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

        content = (
            "---\nschema_version: '1.0'\ndomain: goals\nlast_updated: '2026-11-19'\n---\n"
            "# Goals\n- updated\n"
        )
        with patch.object(mcp_server, "_STATE_DIR", tmp_path):
            encrypted = mcp_server._write_state_file("goals", content)
        assert encrypted is False
        assert (tmp_path / "goals.md").read_text() == content

    def test_encrypts_sensitive_domain(self, tmp_path):
        import mcp_server

        mock_vault = MagicMock()
        mock_vault.read_versioned.return_value = ("# Finance\n", "a" * 64)

        with patch.object(mcp_server, "_STATE_DIR", tmp_path), \
             patch("foundation.get_age_vault", return_value=mock_vault):
            encrypted = mcp_server._write_state_file("finance", "# Finance")

        assert encrypted is True
        mock_vault.write.assert_called_once_with(
            "finance", "# Finance", expected_revision="a" * 64
        )


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

    def test_legacy_effect_tools_are_inert_previews_or_fixed_denials(self):
        import mcp_server

        with patch.object(mcp_server, "_audit"), \
             patch("builtins.__import__", side_effect=AssertionError("an effect module was imported")):
            email = json.loads(mcp_server.artha_send_email(
                "synthetic@example.invalid", "Subject", "Body", approved=True, archive=True
            ))
            todo = json.loads(mcp_server.artha_todo_sync(approved=True, dry_run=False))
            skills = json.loads(mcp_server.artha_run_skills("synthetic-skill"))

        assert email == {
            "status": "preview",
            "action_type": "email_send",
            "effect": "none",
            "payload": {
                "to": "synthetic@example.invalid",
                "subject": "Subject",
                "body": "Body",
                "archive": True,
            },
            "to_apply": "Use the ActionQueue CLI review path; MCP never enqueues or executes this effect.",
        }
        assert todo == {
            "status": "preview",
            "action_type": "todo_sync",
            "effect": "none",
            "payload": {"dry_run": False},
            "to_apply": "Use the ActionQueue CLI review path; MCP never enqueues or executes this effect.",
        }
        assert skills == {"error": "skill execution is held"}

    def test_kb_reads_fail_closed_before_private_graph_access(self):
        import mcp_server

        calls = (
            lambda: mcp_server.artha_kb_query("entity"),
            lambda: mcp_server.artha_kb_context("name"),
            lambda: mcp_server.artha_kb_search("query"),
            lambda: mcp_server.artha_kb_recent_changes(),
            lambda: mcp_server.artha_kb_stale(),
            lambda: mcp_server.artha_kb_global("question"),
            lambda: mcp_server.artha_kb_episodes(),
        )
        with patch.object(mcp_server, "_audit"), \
             patch.object(mcp_server, "_check_rate_limit", side_effect=AssertionError), \
             patch("lib.knowledge_graph.get_kb", side_effect=AssertionError):
            for call in calls:
                assert json.loads(call()) == {
                    "error": "knowledge graph read authorization unavailable"
                }

    def test_state_write_is_not_advertised_as_an_mcp_tool(self):
        import mcp_server
        assert "artha_write_state" not in mcp_server.mcp.registered_tools
        assert {"artha_send_email", "artha_todo_sync", "artha_run_skills"} <= mcp_server.mcp.registered_tools

    def test_discovery_obeys_rate_limiter_denial(self):
        import mcp_server

        denied = json.dumps({"error": "Rate limit exceeded. Try again shortly.", "tool": "artha_list_connectors"})
        with patch.object(mcp_server, "_check_rate_limit", return_value=denied), \
             patch("pipeline.load_connectors_config", side_effect=AssertionError):
            assert mcp_server.artha_list_connectors() == denied

    @pytest.mark.parametrize("call,tool", [
        (lambda mod: mod.artha_health_check(), "artha_health_check"),
        (lambda mod: mod.artha_pii_scan("synthetic"), "artha_pii_scan"),
        (lambda mod: mod.artha_preflight(), "artha_preflight"),
    ])
    def test_all_legacy_read_tools_obey_limiter_denial(self, call, tool):
        import mcp_server
        denied = json.dumps({"error": "Rate limit exceeded. Try again shortly.", "tool": tool})
        with patch.object(mcp_server, "_check_rate_limit", return_value=denied), \
             patch.object(mcp_server, "_audit", side_effect=AssertionError):
            assert call(mcp_server) == denied

    def test_error_surfaces_do_not_echo_provider_exceptions(self):
        import mcp_server
        with patch.object(mcp_server, "_check_rate_limit", return_value=None), \
             patch("pipeline.load_connectors_config", side_effect=RuntimeError("synthetic-secret-canary")), \
             patch.object(mcp_server, "_audit"):
            assert json.loads(mcp_server.artha_health_check()) == {
                "error": "connector health checks unavailable"
            }

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
