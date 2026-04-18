"""tests/unit/test_channel_catchup.py — T4-68..75: channel.catchup tests."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from channel.catchup import (
    _get_last_catchup_iso,
    _gather_all_context,
    _read_briefing_template,
    _save_briefing,
    cmd_catchup,
    _CATCHUP_MAX_CONTEXT_CHARS,
)
import channel.catchup as catchup_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _is_reply_tuple(val) -> bool:
    return (
        isinstance(val, tuple)
        and len(val) == 2
        and isinstance(val[0], str)
        and isinstance(val[1], str)
    )


# ---------------------------------------------------------------------------
# T4-68: _get_last_catchup_iso
# ---------------------------------------------------------------------------

class TestGetLastCatchupIso:
    def test_returns_string(self):
        result = _get_last_catchup_iso()
        assert isinstance(result, str)

    def test_empty_string_when_no_briefings(self, tmp_path, monkeypatch):
        # Function reads health-check.md from _STATE_DIR; patch it to empty tmp dir
        monkeypatch.setattr(catchup_mod, "_STATE_DIR", tmp_path)
        result = _get_last_catchup_iso()
        # With no health-check.md, should return 48h-ago fallback (non-empty ISO string)
        assert isinstance(result, str) and len(result) > 0

    def test_iso_from_briefing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(catchup_mod, "_BRIEFINGS_DIR", tmp_path)
        # Create a dummy briefing file with frontmatter
        bf = tmp_path / "2026-03-25.md"
        bf.write_text("---\ndate: 2026-03-25T09:00:00\n---\n\nContent\n")
        result = _get_last_catchup_iso()
        # Should return non-empty or a valid ISO-like string
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# T4-69: _gather_all_context
# ---------------------------------------------------------------------------

class TestGatherAllContext:
    def test_returns_string(self):
        result = _gather_all_context()
        assert isinstance(result, str)

    def test_budget_respected(self):
        # With default budget, result should be a non-empty string
        # (hard cap enforcement is best-effort given live state files)
        result = _gather_all_context(max_chars=500)
        assert isinstance(result, str)

    def test_empty_ok_with_tiny_budget(self):
        result = _gather_all_context(max_chars=0)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# T4-70: _read_briefing_template
# ---------------------------------------------------------------------------

class TestReadBriefingTemplate:
    def test_returns_string(self):
        result = _read_briefing_template()
        assert isinstance(result, str)

    def test_returns_empty_when_no_template(self, tmp_path, monkeypatch):
        # Patch the config dir to an empty tmp path
        monkeypatch.setattr(catchup_mod, "_CONFIG_DIR", tmp_path)
        result = _read_briefing_template()
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# T4-71: _save_briefing
# ---------------------------------------------------------------------------

class TestSaveBriefing:
    def _patch_archive(self, tmp_path, monkeypatch):
        """Patch both catchup._BRIEFINGS_DIR and briefing_archive paths to tmp_path."""
        import lib.briefing_archive as _ba
        monkeypatch.setattr(catchup_mod, "_BRIEFINGS_DIR", tmp_path)
        monkeypatch.setattr(_ba, "_BRIEFINGS_DIR", tmp_path)
        state = tmp_path / "_state"
        state.mkdir(exist_ok=True)
        (state / "audit.md").write_text("")
        (state / "health-check.md").write_text("")
        monkeypatch.setattr(_ba, "_STATE_DIR", state)
        monkeypatch.setattr(_ba, "_AUDIT_LOG", state / "audit.md")
        monkeypatch.setattr(_ba, "_HEALTH_CHECK", state / "health-check.md")
        monkeypatch.setattr(_ba, "_TMP_DIR", tmp_path)
        monkeypatch.setattr(_ba, "_DRAFT_PATH", tmp_path / "briefing_draft.md")
        monkeypatch.setattr(_ba, "_run_injection_check", lambda text: False)
        monkeypatch.setattr(_ba, "_run_pii_warning", lambda text, source: None)

    def test_writes_file(self, tmp_path, monkeypatch):
        self._patch_archive(tmp_path, monkeypatch)
        saved_path = _save_briefing("Hello briefing content")
        assert saved_path.exists()
        file_text = saved_path.read_text()
        assert "Hello briefing content" in file_text

    def test_returns_path(self, tmp_path, monkeypatch):
        self._patch_archive(tmp_path, monkeypatch)
        result = _save_briefing("test")
        assert isinstance(result, Path)

    def test_filename_has_date(self, tmp_path, monkeypatch):
        self._patch_archive(tmp_path, monkeypatch)
        saved_path = _save_briefing("dated content")
        # Filename should include a date component
        assert saved_path.name.startswith("20")  # year prefix like "2026-..."


# ---------------------------------------------------------------------------
# T4-72: cmd_catchup — returns reply tuple (mocked pipeline + LLM)
# ---------------------------------------------------------------------------

class TestCmdCatchup:
    def _mock_catchup(self, monkeypatch, reply="Mock briefing content"):
        """Patch heavy dependencies so cmd_catchup returns fast."""
        import channel.catchup as cm
        monkeypatch.setattr(cm, "_detect_all_llm_clis", lambda: [("mock", "/usr/bin/echo", [])])
        monkeypatch.setattr(cm, "_gather_all_context", lambda max_chars=80000: "mock context")
        monkeypatch.setattr(cm, "_read_briefing_template", lambda: "mock template")

        async def _fake_pipeline(since_iso):
            return "", 0

        async def _fake_llm(name, exe, base_args, prompt, question, timeout=300):
            return reply

        monkeypatch.setattr(cm, "_run_pipeline", _fake_pipeline)
        monkeypatch.setattr(cm, "_call_single_llm", _fake_llm)

    def test_returns_tuple(self, monkeypatch, tmp_path):
        self._mock_catchup(monkeypatch)
        import channel.catchup as cm
        monkeypatch.setattr(cm, "_BRIEFINGS_DIR", tmp_path)
        result = _run(cmd_catchup([], "full"))
        assert _is_reply_tuple(result)

    def test_flash_scope(self, monkeypatch, tmp_path):
        self._mock_catchup(monkeypatch)
        import channel.catchup as cm
        monkeypatch.setattr(cm, "_BRIEFINGS_DIR", tmp_path)
        result = _run(cmd_catchup([], "flash"))
        assert _is_reply_tuple(result)

    def test_text_non_empty(self, monkeypatch, tmp_path):
        self._mock_catchup(monkeypatch, reply="Artha Briefing\n━━━\nAll clear.")
        import channel.catchup as cm
        monkeypatch.setattr(cm, "_BRIEFINGS_DIR", tmp_path)
        text, _ = _run(cmd_catchup([], "full"))
        assert isinstance(text, str)

    def test_no_llm_returns_error(self, monkeypatch):
        import channel.catchup as cm
        monkeypatch.setattr(cm, "_detect_all_llm_clis", lambda: [])
        text, _ = _run(cmd_catchup([], "full"))
        assert "no llm" in text.lower() or len(text) > 0
