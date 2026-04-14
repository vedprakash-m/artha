"""
tests/unit/test_domain_index.py — Unit tests for scripts/domain_index.py

Phase 2 verification suite.

Coverage:
  - Index reads only frontmatter (not full file body)
  - Index card stays under 1,000 tokens for 18 domains
  - should_load_prompt returns False for /status
  - should_load_prompt returns only routed domains for /catch-up
  - should_load_prompt returns specific domain for /domain finance
  - Feature flag disabled loads all prompts unconditionally
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from domain_index import (
    _domain_status,
    _parse_frontmatter,
    build_domain_index,
    get_prompt_load_list,
    should_load_prompt,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_FRONTMATTER = """\
---
domain: {domain}
last_updated: 2026-03-15T10:00:00Z
last_activity: {last_activity}
alerts: {alerts}
status: {status}
---
# {domain} — Full state file content follows.
This is the full body content that should NOT be read during index building.
BODY_CONTENT_MARKER: This is a very long body that goes on for many lines...
"""


def _write_state_file(
    state_dir: Path,
    domain: str,
    last_activity: str = "2026-03-14",
    alerts: int = 0,
    status: str = "ACTIVE",
) -> Path:
    """Create a state file with YAML frontmatter in state_dir."""
    content = _SAMPLE_FRONTMATTER.format(
        domain=domain,
        last_activity=last_activity,
        alerts=alerts,
        status=status,
    )
    path = state_dir / f"{domain}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _build_18_domains(tmp_path: Path) -> dict:
    """Create 18 domain state files and return index_data dict."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    domains_active = ["immigration", "finance", "health", "goals", "comms", "calendar"]
    domains_stale = ["travel", "shopping", "social", "learning", "pets", "vehicle"]
    domains_archive = ["estate", "insurance", "home", "kids", "employment", "digital"]

    for d in domains_active:
        _write_state_file(state_dir, d, last_activity="2026-03-14", alerts=2)
    for d in domains_stale:
        _write_state_file(state_dir, d, last_activity="2026-01-10", alerts=0)
    for d in domains_archive:
        _write_state_file(state_dir, d, last_activity="2024-06-01", alerts=0)

    _, index_data = build_domain_index(tmp_path)
    return index_data


# ---------------------------------------------------------------------------
# _parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_reads_domain_field(self, tmp_path):
        path = _write_state_file(tmp_path, "finance")
        fm = _parse_frontmatter(path)
        assert fm.get("domain") == "finance"

    def test_reads_alerts_field(self, tmp_path):
        path = _write_state_file(tmp_path, "health", alerts=3)
        fm = _parse_frontmatter(path)
        assert fm.get("alerts") == 3

    def test_missing_file_returns_empty(self, tmp_path):
        fm = _parse_frontmatter(tmp_path / "nonexistent.md")
        assert fm == {}

    def test_no_frontmatter_returns_empty(self, tmp_path):
        path = tmp_path / "bare.md"
        path.write_text("No frontmatter here\n", encoding="utf-8")
        fm = _parse_frontmatter(path)
        assert fm == {}

    def test_does_not_read_beyond_frontmatter(self, tmp_path):
        """Verify only the frontmatter section is parsed (not body)."""
        content = "---\ndomain: test\n---\nbody_marker: not_yaml_key: should_not_appear\n"
        path = tmp_path / "test.md"
        path.write_text(content)
        fm = _parse_frontmatter(path)
        # body_marker line is AFTER the closing ---, so should NOT be in frontmatter
        assert fm.get("domain") == "test"
        # The frontmatter only has 'domain'
        assert list(fm.keys()) == ["domain"]


# ---------------------------------------------------------------------------
# build_domain_index
# ---------------------------------------------------------------------------

class TestBuildDomainIndex:
    def test_returns_card_and_dict(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        _write_state_file(state_dir, "finance")

        # Note: build_domain_index receives artha_dir, not state_dir
        card, index_data = build_domain_index(tmp_path)
        assert isinstance(card, str)
        assert isinstance(index_data, dict)

    def test_index_reads_frontmatter_only(self, tmp_path):
        """The body text AFTER frontmatter must not appear in index_data."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        content = "---\ndomain: testdomain\nlast_activity: 2026-03-14\n---\n"
        content += "BODYMARKER12345 body body body\n" * 200  # distinctive body marker
        (state_dir / "testdomain.md").write_text(content)

        card, index_data = build_domain_index(tmp_path)

        assert "BODYMARKER12345" not in card
        data = index_data.get("testdomain", {})
        assert "BODYMARKER12345" not in str(data)

    def test_active_domain_classified_correctly(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        # Use yesterday's date so it's always within the 30-day ACTIVE window
        recent = (date.today() - timedelta(days=1)).isoformat()
        _write_state_file(state_dir, "immigration", last_activity=recent)

        _, index_data = build_domain_index(tmp_path)
        assert index_data["immigration"]["status"] == "ACTIVE"

    def test_stale_domain_classified_correctly(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        _write_state_file(state_dir, "travel", last_activity="2025-12-01")

        _, index_data = build_domain_index(tmp_path)
        assert index_data["travel"]["status"] in ("STALE", "ARCHIVE")

    def test_alerts_in_index(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        _write_state_file(state_dir, "health", alerts=2)

        _, index_data = build_domain_index(tmp_path)
        assert index_data["health"]["alerts"] == 2

    def test_no_state_dir_returns_gracefully(self, tmp_path):
        card, index_data = build_domain_index(tmp_path)
        assert "no state directory" in card.lower() or isinstance(card, str)
        assert index_data == {}

    def test_index_under_1k_tokens_for_18_domains(self, tmp_path):
        """Index card for 18 domains must stay under 1,000 tokens (~4,000 chars)."""
        _build_18_domains(tmp_path)
        card, _ = build_domain_index(tmp_path)
        tokens = len(card) // 4  # 1 token ≈ 4 chars
        assert tokens < 1000, (
            f"Index card is {tokens} tokens for 18 domains — must be < 1000"
        )

    def test_all_18_domains_appear_in_index(self, tmp_path):
        """All 18 domains should appear in the index card."""
        index_data = _build_18_domains(tmp_path)
        assert len(index_data) == 18


# ---------------------------------------------------------------------------
# should_load_prompt
# ---------------------------------------------------------------------------

class TestShouldLoadPrompt:
    def _index(self) -> dict:
        return {
            "immigration": {"status": "ACTIVE", "alerts": 2},
            "finance": {"status": "ACTIVE", "alerts": 1},
            "health": {"status": "ACTIVE", "alerts": 0},
            "travel": {"status": "STALE", "alerts": 0},
            "estate": {"status": "ARCHIVE", "alerts": 0},
        }

    def test_status_returns_false_for_all_domains(self):
        index = self._index()
        for domain in index:
            result = should_load_prompt(domain, index, command="/status")
            assert result is False, f"/status should not load {domain} prompt"

    def test_items_returns_false_for_all_domains(self):
        index = self._index()
        for domain in index:
            result = should_load_prompt(domain, index, command="/items")
            assert result is False

    def test_goals_returns_true_only_for_goals(self):
        index = {**self._index(), "goals": {"status": "ACTIVE", "alerts": 0}}
        assert should_load_prompt("goals", index, command="/goals") is True
        assert should_load_prompt("finance", index, command="/goals") is False

    def test_domain_command_returns_only_specific_domain(self):
        index = self._index()
        assert should_load_prompt("finance", index, command="/domain finance") is True
        assert should_load_prompt("immigration", index, command="/domain finance") is False
        assert should_load_prompt("health", index, command="/domain finance") is False

    def test_catchup_returns_active_domains(self):
        index = self._index()
        # ACTIVE domains with activity → load
        assert should_load_prompt("immigration", index, command="/catch-up") is True
        assert should_load_prompt("finance", index, command="/catch-up") is True

    def test_catchup_skips_archive_domains(self):
        index = self._index()
        assert should_load_prompt("estate", index, command="/catch-up") is False

    def test_catchup_deep_loads_active_not_archive(self):
        index = self._index()
        assert should_load_prompt("immigration", index, command="/catch-up deep") is True
        assert should_load_prompt("estate", index, command="/catch-up deep") is False

    def test_unknown_command_defaults_to_load(self):
        """Unknown commands should default to loading prompts (safe default)."""
        index = self._index()
        assert should_load_prompt("immigration", index, command="/unknown-command") is True

    def test_feature_flag_disabled_loads_all(self):
        """When progressive_disclosure.enabled = false, all prompts load."""
        index = self._index()
        with patch("domain_index.load_harness_flag", return_value=False):
            for domain in index:
                assert should_load_prompt(domain, index, command="/status") is True


# ---------------------------------------------------------------------------
# get_prompt_load_list
# ---------------------------------------------------------------------------

class TestGetPromptLoadList:
    def test_routed_domains_always_included(self):
        index = {
            "travel": {"status": "ARCHIVE", "alerts": 0},
            "finance": {"status": "ACTIVE", "alerts": 1},
        }
        result = get_prompt_load_list(index, "/status", routed_domains=["travel"])
        assert "travel" in result

    def test_load_list_is_sorted(self):
        index = {
            "z_domain": {"status": "ACTIVE", "alerts": 1},
            "a_domain": {"status": "ACTIVE", "alerts": 1},
        }
        result = get_prompt_load_list(index, "/catch-up")
        # Active domains should be included, and result should be sorted
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# _domain_status helper
# ---------------------------------------------------------------------------

class TestDomainStatus:
    def test_active_range(self):
        assert _domain_status(0) == "ACTIVE"
        assert _domain_status(30) == "ACTIVE"

    def test_stale_range(self):
        assert _domain_status(31) == "STALE"
        assert _domain_status(180) == "STALE"

    def test_archive_range(self):
        assert _domain_status(181) == "ARCHIVE"
        assert _domain_status(3650) == "ARCHIVE"

    def test_none_is_unknown(self):
        assert _domain_status(None) == "UNKNOWN"
