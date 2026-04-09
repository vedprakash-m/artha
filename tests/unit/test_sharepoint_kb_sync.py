"""tests/unit/test_sharepoint_kb_sync.py

Unit tests for scripts/sharepoint_kb_sync.py

Tests cover:
  - _load_sp_config(): returns None when disabled, None when file absent,
    config dict when enabled
  - _infer_domain(): keyword routing for finance / engineering / health /
    operations / knowledge and the default fallback
  - _clear_delta_links(): removes delta_links and shared_with_me keys,
    atomic write
  - sync(): dry-run suppresses KB writes; live mode calls add_episode +
    upsert_entity + write_markdown_stub per doc; health_check failure
    leaves stats unchanged; per-doc errors are non-fatal
  - _safe_stem(): filesystem-safe name, max-60-char truncation, special
    character replacement
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

_ARTHA_ROOT  = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import sharepoint_kb_sync as sks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_doc(**kwargs) -> dict:
    """Return a minimal document dict as yielded by sp.fetch()."""
    base = {
        "id":            "item1",
        "drive_item_id": "drv1:item1",
        "name":          "doc.md",
        "webUrl":        "https://sp.example.com/doc.md",
        "web_url":       "https://sp.example.com/doc.md",
        "site_name":     "Engineering",
        "library_path":  "/sites/eng/Shared Documents",
        "content_text":  "# Engineering Doc\n\nContent.\n",
        "text":          "# Engineering Doc\n\nContent.\n",
        "content_hash":  "abc123",
        "source":        "delta",
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# _load_sp_config
# ---------------------------------------------------------------------------

class TestLoadSpConfig:
    def test_returns_none_when_enabled_false(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "connectors.yaml"
        cfg_file.write_text(
            "connectors:\n"
            "  sharepoint_docs:\n"
            "    enabled: false\n"
            "    fetch:\n"
            "      handler: scripts/connectors/msgraph_sharepoint.py\n"
            "      sites: []\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(sks, "_CONFIG_FILE", cfg_file)
        result = sks._load_sp_config()
        assert result is None

    def test_returns_none_when_file_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sks, "_CONFIG_FILE", tmp_path / "connectors.yaml")
        result = sks._load_sp_config()
        assert result is None

    def test_returns_config_when_enabled(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "connectors.yaml"
        cfg_file.write_text(
            "connectors:\n"
            "  sharepoint_docs:\n"
            "    enabled: true\n"
            "    fetch:\n"
            "      handler: scripts/connectors/msgraph_sharepoint.py\n"
            "      sites: []\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(sks, "_CONFIG_FILE", cfg_file)
        result = sks._load_sp_config()
        assert result is not None
        assert result["enabled"] is True


# ---------------------------------------------------------------------------
# _infer_domain
# ---------------------------------------------------------------------------

class TestInferDomain:
    @pytest.mark.parametrize("site_name,library_path,name,expected", [
        ("Finance",       "/sites/fin/Shared Documents", "budget.md",     "finance"),
        ("Engineering",   "/sites/eng/docs",             "design.md",     "engineering"),
        ("Legal Team",    "/sites/legal",                "contract.md",   "legal"),
        ("Operations",    "/sites/ops",                  "runbook.md",    "operations"),
        ("Knowledge Base","/sites/kb",                   "guide.md",      "knowledge"),
        ("MyTeam",        "/sites/myteam/Shared",        "random.docx",   "knowledge"),  # default
    ])
    def test_keyword_routing(self, site_name, library_path, name, expected):
        doc = _make_doc(site_name=site_name, library_path=library_path, name=name)
        assert sks._infer_domain(doc) == expected


# ---------------------------------------------------------------------------
# _safe_stem
# ---------------------------------------------------------------------------

class TestSafeStem:
    def test_normal_name(self):
        assert sks._safe_stem("notes.md") == "notes"

    def test_special_chars_replaced(self):
        stem = sks._safe_stem("my file (v2) <final>.docx")
        assert " " not in stem
        assert "(" not in stem
        assert "<" not in stem

    def test_max_60_chars(self):
        long_name = "a" * 100 + ".md"
        stem = sks._safe_stem(long_name)
        assert len(stem) <= 60


# ---------------------------------------------------------------------------
# _clear_delta_links
# ---------------------------------------------------------------------------

class TestClearDeltaLinks:
    def test_removes_delta_links_and_shared(self, tmp_path):
        state_dir  = tmp_path / "state" / "connectors"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "sharepoint_docs_state.yaml"
        state_file.write_text(
            "ingested_items: {}\n"
            "delta_links:\n"
            "  site1:drv1: https://graph.microsoft.com/v1.0/...\n"
            "shared_with_me:\n"
            "  last_seen_shared_at: '2026-01-01T00:00:00Z'\n"
            "last_run_at: '2026-01-01T00:00:00Z'\n",
            encoding="utf-8",
        )
        import connectors.msgraph_sharepoint as sp_mod
        with patch.object(sp_mod, "_SP_STATE_FILE", state_file):
            sks._clear_delta_links()

        import yaml
        reloaded = yaml.safe_load(state_file.read_text(encoding="utf-8"))
        assert "delta_links"    not in reloaded or reloaded.get("delta_links") in (None, {})
        assert "shared_with_me" not in reloaded or reloaded.get("shared_with_me") in (None, {})

    def test_noop_when_state_absent(self, tmp_path):
        import connectors.msgraph_sharepoint as sp_mod
        missing = tmp_path / "nonexistent_state.yaml"
        with patch.object(sp_mod, "_SP_STATE_FILE", missing):
            sks._clear_delta_links()  # should not raise


# ---------------------------------------------------------------------------
# sync()
# ---------------------------------------------------------------------------

class TestSync:
    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _mock_sp_module(self):
        m = MagicMock()
        m.health_check.return_value = True
        m.fetch.return_value = iter([_make_doc()])
        return m

    def _mock_kgraph(self):
        kg = MagicMock()
        kg.add_episode.return_value  = 42
        kg.upsert_entity.return_value = "ent-1"
        return kg

    def _sp_cfg(self):
        return {
            "enabled":  True,
            "auth":     {"token_file": ".tokens/t.json"},
            "fetch":    {"sites": [], "include_shared_with_me": False},
            "output":   {"notes_dir": "state/connectors/sharepoint_notes"},
        }

    # ------------------------------------------------------------------
    # dry-run
    # ------------------------------------------------------------------

    def test_dry_run_suppresses_kb_writes(self, tmp_path, monkeypatch):
        mock_sp = self._mock_sp_module()
        mock_kg = self._mock_kgraph()

        monkeypatch.setattr(sks, "_load_sp_config", self._sp_cfg)
        monkeypatch.setattr(sks, "_load_token", lambda *a: "tok")

        with patch("connectors.msgraph_sharepoint.fetch", return_value=iter([_make_doc()])):
            with patch("connectors.msgraph_sharepoint.health_check", return_value=True):
                with patch("sharepoint_kb_sync.get_kb", return_value=mock_kg):
                    with patch("sharepoint_kb_sync.DocumentExtractor") as mock_de:
                        mock_de.from_text.return_value.entities = []
                        with patch("sharepoint_kb_sync.write_markdown_stub") as mock_stub:
                            stats = sks.sync(dry_run=True, verbose=False, artha_dir=tmp_path)

        mock_kg.add_episode.assert_not_called()
        mock_stub.assert_not_called()

    # ------------------------------------------------------------------
    # live mode
    # ------------------------------------------------------------------

    def test_live_mode_calls_kb_pipeline(self, tmp_path, monkeypatch):
        notes_dir = tmp_path / "state" / "connectors" / "sharepoint_notes"
        notes_dir.mkdir(parents=True)

        mock_kg = self._mock_kgraph()
        mock_extractor_instance = MagicMock()
        mock_extractor_instance.entities = []

        monkeypatch.setattr(sks, "_load_sp_config", self._sp_cfg)
        monkeypatch.setattr(sks, "_load_token", lambda *a: "tok")
        monkeypatch.setattr(sks, "_NOTES_DIR", notes_dir)

        with patch("connectors.msgraph_sharepoint.fetch", return_value=iter([_make_doc()])):
            with patch("connectors.msgraph_sharepoint.health_check", return_value=True):
                with patch("sharepoint_kb_sync.get_kb", return_value=mock_kg):
                    with patch("sharepoint_kb_sync.DocumentExtractor") as mock_de_cls:
                        mock_de_cls.from_text.return_value = mock_extractor_instance
                        with patch("sharepoint_kb_sync.write_markdown_stub") as mock_stub:
                            stats = sks.sync(dry_run=False, verbose=False, artha_dir=tmp_path)

        mock_kg.add_episode.assert_called()
        mock_stub.assert_called()

    # ------------------------------------------------------------------
    # health_check failure
    # ------------------------------------------------------------------

    def test_health_check_fail_returns_early(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sks, "_load_sp_config", self._sp_cfg)
        monkeypatch.setattr(sks, "_load_token", lambda *a: "tok")

        with patch("connectors.msgraph_sharepoint.health_check", return_value=False):
            with patch("sharepoint_kb_sync.get_kb") as mock_kg_fn:
                stats = sks.sync(dry_run=False, verbose=False, artha_dir=tmp_path)

        mock_kg_fn.assert_not_called()
        assert stats.get("docs_ingested", 0) == 0

    # ------------------------------------------------------------------
    # per-doc errors are non-fatal
    # ------------------------------------------------------------------

    def test_per_doc_error_is_non_fatal(self, tmp_path, monkeypatch):
        notes_dir = tmp_path / "state" / "connectors" / "sharepoint_notes"
        notes_dir.mkdir(parents=True)

        monkeypatch.setattr(sks, "_load_sp_config", self._sp_cfg)
        monkeypatch.setattr(sks, "_load_token", lambda *a: "tok")
        monkeypatch.setattr(sks, "_NOTES_DIR", notes_dir)

        call_count = {"n": 0}

        def _flaky_add_episode(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("transient DB error")
            return 42

        mock_kg = self._mock_kgraph()
        mock_kg.add_episode.side_effect = _flaky_add_episode

        mock_extractor_instance = MagicMock()
        mock_extractor_instance.entities = []

        docs = [
            _make_doc(drive_item_id="drv1:item1"),
            _make_doc(drive_item_id="drv1:item2"),
        ]

        with patch("connectors.msgraph_sharepoint.health_check", return_value=True):
            with patch("connectors.msgraph_sharepoint.fetch", return_value=iter(docs)):
                with patch("sharepoint_kb_sync.get_kb", return_value=mock_kg):
                    with patch("sharepoint_kb_sync.DocumentExtractor") as mock_de_cls:
                        mock_de_cls.from_text.return_value = mock_extractor_instance
                        with patch("sharepoint_kb_sync.write_markdown_stub"):
                            stats = sks.sync(dry_run=False, verbose=False, artha_dir=tmp_path)

        # Second doc should have been ingested despite first doc failing
        assert len(stats.get("errors", [])) >= 1
        assert stats.get("docs_ingested", 0) >= 1

    # ------------------------------------------------------------------
    # config disabled
    # ------------------------------------------------------------------

    def test_returns_empty_stats_when_config_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sks, "_load_sp_config", lambda: None)

        stats = sks.sync(dry_run=False, verbose=False, artha_dir=tmp_path)
        assert stats.get("docs_ingested", 0) == 0

    def test_returns_empty_stats_when_token_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sks, "_load_sp_config", self._sp_cfg)
        monkeypatch.setattr(sks, "_load_token", lambda *a: None)

        stats = sks.sync(dry_run=False, verbose=False, artha_dir=tmp_path)
        assert stats.get("docs_ingested", 0) == 0
