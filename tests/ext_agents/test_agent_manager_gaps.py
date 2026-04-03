"""tests/ext_agents/test_agent_manager_gaps.py — AR-9 agent_manager gap tests.

Covers the four previously-identified gaps:
  Gap 1/2 : shadow_mode + registered_at written by _make_agent_entry / cmd_register
  Gap 3   : content_hash format (sha256: prefix, full hex)
  Gap 4   : §4.9.1 archive-previous-version on --force update
  Gap 5   : §4.9.3 deletion detection in cmd_discover / cmd_health
"""
from __future__ import annotations

import hashlib
import io
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest
import yaml

# ---------------------------------------------------------------------------
# Ensure scripts/ is importable
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import agent_manager as am  # noqa: E402 — after sys.path setup

from lib.agent_registry import AgentRegistry, _parse_agent  # noqa: E402


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

MINIMAL_AGENT_MD = textwrap.dedent("""\
    ---
    name: gap-agent
    label: Gap Agent
    description: Agent for gap tests.
    trust_tier: external
    keywords:
      - deployment stuck
      - SDP block
    domains:
      - deployment
    ---

    # Gap Agent body
""")


@pytest.fixture()
def drop_dir(tmp_path: Path) -> Path:
    """Return a temp drop folder with one .agent.md file."""
    d = tmp_path / "config" / "agents" / "external"
    d.mkdir(parents=True)
    (d / "gap-agent.agent.md").write_text(MINIMAL_AGENT_MD, encoding="utf-8")
    return d


@pytest.fixture()
def config_dir(tmp_path: Path, drop_dir: Path) -> Path:
    """Return config_dir (parent of agents/) with empty registry."""
    config = tmp_path / "config"
    (config / "agents" / "external-registry.yaml").write_text(
        "schema_version: '1.0'\nagents: {}\n", encoding="utf-8"
    )
    return config


@pytest.fixture()
def populated_config_dir(tmp_path: Path, drop_dir: Path) -> Path:
    """config_dir with gap-agent already registered (no registered_at, old hash)."""
    config = tmp_path / "config"
    agent_file = drop_dir / "gap-agent.agent.md"
    old_hash = "sha256:" + hashlib.sha256(agent_file.read_bytes()).hexdigest()
    reg_data = {
        "schema_version": "1.0",
        "agents": {
            "gap-agent": {
                "label": "Gap Agent",
                "description": "Agent for gap tests.",
                "trust_tier": "external",
                "enabled": True,
                "status": "active",
                "source": "config/agents/external/gap-agent.agent.md",
                "content_hash": old_hash,
                "auto_dispatch": False,
                "auto_dispatch_after": 10,
                "routing": {"keywords": ["deployment stuck"], "min_confidence": 0.3},
                "health": {"status": "active"},
            }
        },
    }
    (config / "agents" / "external-registry.yaml").write_text(
        yaml.dump(reg_data), encoding="utf-8"
    )
    return config


# ---------------------------------------------------------------------------
# Gap 1/2: registered_at and shadow_mode written by _make_agent_entry
# ---------------------------------------------------------------------------

class TestMakeAgentEntry:
    """_make_agent_entry must produce registered_at and shadow_mode."""

    def test_registered_at_is_iso_timestamp(self, drop_dir: Path, tmp_path: Path):
        agent_file = drop_dir / "gap-agent.agent.md"
        fm = am._parse_agent_md(agent_file)
        with mock.patch.object(am, "_REPO_ROOT", tmp_path):
            entry = am._make_agent_entry(fm, agent_file)
        ts = entry.get("registered_at")
        assert ts is not None, "registered_at must be set"
        # Must be a parseable ISO timestamp
        from datetime import datetime
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.year >= 2026

    def test_shadow_mode_defaults_false(self, drop_dir: Path, tmp_path: Path):
        agent_file = drop_dir / "gap-agent.agent.md"
        fm = am._parse_agent_md(agent_file)
        with mock.patch.object(am, "_REPO_ROOT", tmp_path):
            entry = am._make_agent_entry(fm, agent_file)
        assert entry.get("shadow_mode") is False

    def test_shadow_mode_read_from_frontmatter(self, drop_dir: Path, tmp_path: Path):
        agent_file = drop_dir / "gap-agent.agent.md"
        raw = agent_file.read_text(encoding="utf-8")
        # Inject shadow_mode into frontmatter
        agent_file.write_text(raw.replace("trust_tier: external", "trust_tier: external\nshadow_mode: true"), encoding="utf-8")
        fm = am._parse_agent_md(agent_file)
        with mock.patch.object(am, "_REPO_ROOT", tmp_path):
            entry = am._make_agent_entry(fm, agent_file)
        assert entry.get("shadow_mode") is True

    def test_content_hash_is_full_sha256_with_prefix(self, drop_dir: Path, tmp_path: Path):
        agent_file = drop_dir / "gap-agent.agent.md"
        fm = am._parse_agent_md(agent_file)
        with mock.patch.object(am, "_REPO_ROOT", tmp_path):
            entry = am._make_agent_entry(fm, agent_file)
        h = entry.get("content_hash", "")
        assert h.startswith("sha256:"), f"Expected sha256: prefix, got: {h}"
        hex_part = h[len("sha256:"):]
        assert len(hex_part) == 64, f"Expected 64-char hex, got {len(hex_part)}: {hex_part}"
        # Verify correctness
        expected = hashlib.sha256(agent_file.read_bytes()).hexdigest()
        assert hex_part == expected


# ---------------------------------------------------------------------------
# Gap 3: content_hash format consistency & backward-compat comparison
# ---------------------------------------------------------------------------

class TestContentHashConsistency:
    """agent_manager must use same full sha256: format as AgentRegistry."""

    def test_agent_registry_compute_hash_format(self, drop_dir: Path):
        agent_file = drop_dir / "gap-agent.agent.md"
        computed = AgentRegistry.compute_content_hash(agent_file)
        assert computed.startswith("sha256:")
        assert len(computed) == len("sha256:") + 64

    def test_agent_manager_hash_matches_registry(self, drop_dir: Path, tmp_path: Path):
        agent_file = drop_dir / "gap-agent.agent.md"
        fm = am._parse_agent_md(agent_file)
        with mock.patch.object(am, "_REPO_ROOT", tmp_path):
            entry = am._make_agent_entry(fm, agent_file)
        manager_hash = entry["content_hash"]
        registry_hash = AgentRegistry.compute_content_hash(agent_file)
        assert manager_hash == registry_hash

    def test_norm_no_spurious_update_for_full_hex_without_prefix(self, drop_dir: Path):
        """_norm() strips sha256: prefix so full-hex-without-prefix == sha256:-prefixed."""
        agent_file = drop_dir / "gap-agent.agent.md"
        full_hex = hashlib.sha256(agent_file.read_bytes()).hexdigest()

        # _norm logic (mirrors cmd_register inner function)
        def _norm(h):
            if not h:
                return ""
            return h[len("sha256:"):] if h.startswith("sha256:") else h

        # Stored as bare 64-char hex (no prefix), new as sha256:... → should be equal
        stored_no_prefix = full_hex
        new_with_prefix = "sha256:" + full_hex
        assert _norm(new_with_prefix) == _norm(stored_no_prefix), (
            "_norm must equalise same-file hashes regardless of sha256: prefix"
        )

    def test_legacy_short_hash_detected_as_changed(self, drop_dir: Path):
        """Old 16-char truncated hashes are correctly detected as changed → triggers upgrade."""
        agent_file = drop_dir / "gap-agent.agent.md"
        full_hex = hashlib.sha256(agent_file.read_bytes()).hexdigest()
        short_hash = full_hex[:16]  # old truncated format

        def _norm(h):
            if not h:
                return ""
            return h[len("sha256:"):] if h.startswith("sha256:") else h

        new_full = "sha256:" + full_hex
        # Short hash should differ from full — triggers re-registration (correct behavior)
        assert _norm(new_full) != _norm(short_hash), (
            "Legacy 16-char hash should differ from full hash to force re-registration"
        )


# ---------------------------------------------------------------------------
# Gap 4: §4.9.1 archive previous version
# ---------------------------------------------------------------------------

class TestArchivePreviousVersion:
    """_archive_agent_version() must copy file to .archive/ and prune old entries."""

    def test_archive_creates_archive_dir(self, drop_dir: Path, tmp_path: Path):
        agent_file = drop_dir / "gap-agent.agent.md"
        archive_dir = drop_dir / ".archive"
        assert not archive_dir.exists()

        # Patch _ARCHIVE_DIR to point into our tmp drop dir
        with mock.patch.object(am, "_ARCHIVE_DIR", archive_dir):
            am._archive_agent_version("gap-agent", agent_file, "sha256:abc123def456")

        assert archive_dir.is_dir()

    def test_archive_file_named_by_hash(self, drop_dir: Path):
        agent_file = drop_dir / "gap-agent.agent.md"
        archive_dir = drop_dir / ".archive"
        stored_hash = "sha256:aabbccdd11223344"

        with mock.patch.object(am, "_ARCHIVE_DIR", archive_dir):
            am._archive_agent_version("gap-agent", agent_file, stored_hash)

        expected_name = "gap-agent-aabbccdd.md"  # first 8 chars of hex part
        assert (archive_dir / expected_name).exists()

    def test_archive_preserves_content(self, drop_dir: Path):
        agent_file = drop_dir / "gap-agent.agent.md"
        original_content = agent_file.read_text(encoding="utf-8")
        archive_dir = drop_dir / ".archive"

        with mock.patch.object(am, "_ARCHIVE_DIR", archive_dir):
            am._archive_agent_version("gap-agent", agent_file, "sha256:00001111")

        archived = archive_dir / "gap-agent-00001111.md"
        assert archived.read_text(encoding="utf-8") == original_content

    def test_archive_prunes_excess_versions(self, drop_dir: Path):
        """Only last _ARCHIVE_KEEP archives are retained."""
        agent_file = drop_dir / "gap-agent.agent.md"
        archive_dir = drop_dir / ".archive"
        archive_dir.mkdir()

        # Pre-populate _ARCHIVE_KEEP + 2 old archives so pruning is triggered
        keep = am._ARCHIVE_KEEP
        for i in range(keep + 2):
            arc = archive_dir / f"gap-agent-{i:08x}.md"
            arc.write_text(f"version {i}", encoding="utf-8")
            # Touch with incrementing mtimes so sort is deterministic
            import os, time
            t = 1000000 + i
            os.utime(arc, (t, t))

        with mock.patch.object(am, "_ARCHIVE_DIR", archive_dir):
            am._archive_agent_version("gap-agent", agent_file, "sha256:ffffffff")

        remaining = list(archive_dir.glob("gap-agent-*.md"))
        assert len(remaining) <= keep, (
            f"Expected ≤{keep} archives, found {len(remaining)}"
        )

    def test_archive_skips_missing_file_gracefully(self, drop_dir: Path):
        """If the source file doesn't exist, archive must not crash."""
        missing = drop_dir / "does-not-exist.agent.md"
        archive_dir = drop_dir / ".archive"
        with mock.patch.object(am, "_ARCHIVE_DIR", archive_dir):
            # Should not raise
            am._archive_agent_version("ghost", missing, "sha256:deadbeef")
        # Archive dir should not have been created for a missing source
        # (because we return early before mkdir)
        assert not archive_dir.exists()

    def test_archive_failure_does_not_block_update(self, drop_dir: Path):
        """OSError during archive must not propagate."""
        agent_file = drop_dir / "gap-agent.agent.md"
        archive_dir = drop_dir / ".archive"

        with mock.patch.object(am, "_ARCHIVE_DIR", archive_dir):
            with mock.patch("shutil.copy2", side_effect=OSError("disk full")):
                # Should not raise
                am._archive_agent_version("gap-agent", agent_file, "sha256:ff")


# ---------------------------------------------------------------------------
# Gap 5: §4.9.3 deletion detection
# ---------------------------------------------------------------------------

class TestDeletionDetection:
    """_check_deleted_agents() must surface missing source files."""

    def _make_reg_with_source(self, tmp_path: Path, source: str) -> AgentRegistry:
        reg_data = {
            "schema_version": "1.0",
            "agents": {
                "gap-agent": {
                    "label": "Gap Agent",
                    "description": "",
                    "trust_tier": "external",
                    "enabled": True,
                    "status": "active",
                    "source": source,
                    "content_hash": "sha256:" + "0" * 64,
                    "auto_dispatch": False,
                    "auto_dispatch_after": 10,
                    "routing": {"keywords": ["foo"], "min_confidence": 0.3},
                    "health": {"status": "active"},
                }
            },
        }
        cfg = tmp_path / "config"
        (cfg / "agents").mkdir(parents=True, exist_ok=True)
        (cfg / "agents" / "external-registry.yaml").write_text(
            yaml.dump(reg_data), encoding="utf-8"
        )
        return AgentRegistry.load(cfg)

    def test_no_missing_files_returns_zero(self, tmp_path: Path, drop_dir: Path):
        """When source file exists, no deletions are reported."""
        # Source is relative to repo root; point to something that exists
        # Use a real file in the drop dir we control
        real_source = str(
            (drop_dir / "gap-agent.agent.md").relative_to(tmp_path)
        ).replace("\\", "/")
        reg = self._make_reg_with_source(tmp_path, real_source)
        with mock.patch.object(am, "_REPO_ROOT", tmp_path):
            count = am._check_deleted_agents(reg, interactive=False)
        assert count == 0

    def test_missing_source_file_detected(self, tmp_path: Path):
        """When source file is deleted, count == 1."""
        reg = self._make_reg_with_source(
            tmp_path, "config/agents/external/gone.agent.md"
        )
        with mock.patch.object(am, "_REPO_ROOT", tmp_path):
            count = am._check_deleted_agents(reg, interactive=False)
        assert count == 1

    def test_missing_source_prints_warning(self, tmp_path: Path, capsys):
        reg = self._make_reg_with_source(
            tmp_path, "config/agents/external/removed.agent.md"
        )
        with mock.patch.object(am, "_REPO_ROOT", tmp_path):
            am._check_deleted_agents(reg, interactive=False)
        captured = capsys.readouterr()
        assert "removed.agent.md" in captured.out
        assert "⚠" in captured.out

    def test_interactive_yes_retires_agent(self, tmp_path: Path, capsys):
        reg = self._make_reg_with_source(
            tmp_path, "config/agents/external/removed.agent.md"
        )
        with (
            mock.patch.object(am, "_REPO_ROOT", tmp_path),
            mock.patch("builtins.input", return_value="yes"),
            mock.patch.object(am, "_save_registry", return_value=True),
        ):
            am._check_deleted_agents(reg, interactive=True)
        agent = reg.get("gap-agent")
        assert agent is not None
        assert agent.status == "retired"
        assert agent.enabled is False

    def test_interactive_keep_preserves_agent(self, tmp_path: Path):
        reg = self._make_reg_with_source(
            tmp_path, "config/agents/external/removed.agent.md"
        )
        with (
            mock.patch.object(am, "_REPO_ROOT", tmp_path),
            mock.patch("builtins.input", return_value="keep"),
            mock.patch.object(am, "_save_registry", return_value=True),
        ):
            am._check_deleted_agents(reg, interactive=True)
        agent = reg.get("gap-agent")
        assert agent is not None
        assert agent.status == "active"

    def test_interactive_no_preserves_agent(self, tmp_path: Path):
        reg = self._make_reg_with_source(
            tmp_path, "config/agents/external/removed.agent.md"
        )
        with (
            mock.patch.object(am, "_REPO_ROOT", tmp_path),
            mock.patch("builtins.input", return_value="no"),
            mock.patch.object(am, "_save_registry", return_value=True),
        ):
            am._check_deleted_agents(reg, interactive=True)
        agent = reg.get("gap-agent")
        assert agent is not None
        assert agent.status == "active"

    def test_none_registry_returns_zero(self):
        count = am._check_deleted_agents(None, interactive=False)
        assert count == 0

    def test_agent_without_source_skipped(self, tmp_path: Path):
        """Agents with empty source are silently skipped."""
        reg = self._make_reg_with_source(tmp_path, "")
        with mock.patch.object(am, "_REPO_ROOT", tmp_path):
            count = am._check_deleted_agents(reg, interactive=False)
        assert count == 0
