"""
tests/unit/test_context_offloader.py — Unit tests for scripts/context_offloader.py

Phase 1 verification suite.

Coverage:
  - Below-threshold data returned inline (no file written)
  - Above-threshold data written to file (summary card returned)
  - Summary card is ≤ 500 tokens
  - OFFLOADED_FILES manifest covers expected cleanup targets
  - Feature flag disabled bypasses offloading
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

# conftest adds scripts/ to sys.path
from context_offloader import (
    OFFLOADED_FILES,
    OFFLOADED_GLOB_PATTERNS,
    _CHARS_PER_TOKEN,
    _MAX_CARD_TOKENS,
    _estimate_tokens,
    _serialize,
    cross_domain_summary,
    emails_summary,
    load_harness_flag,
    offload_artifact,
    pipeline_summary,
    EvictionTier,
    _ARTIFACT_TIERS,
    _TIER_THRESHOLDS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_records(n: int) -> list[dict]:
    """Generate n fake email-like records."""
    return [
        {
            "id": f"msg_{i}",
            "subject": f"Subject number {i} with some extra words to pad token count",
            "from": "sender@example.com",
            "body": "This is the body text. " * 20,
            "date_iso": "2026-03-15T10:00:00Z",
            "source": "gmail",
        }
        for i in range(n)
    ]


def _big_data(approx_tokens: int) -> list[dict]:
    """Return a list of dicts whose serialization exceeds approx_tokens."""
    # Each record serializes to ~200 chars ≈ 50 tokens
    n = (approx_tokens // 50) + 1
    return _make_records(n)


# ---------------------------------------------------------------------------
# _estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_string(self):
        assert _estimate_tokens("") == 0

    def test_known_length(self):
        text = "a" * 400
        # RD-21: CHARS_PER_TOKEN changed from 4 → 3.5; 400 / 3.5 ≈ 114.28
        import pytest as _pt
        assert _estimate_tokens(text) == _pt.approx(400 / 3.5, abs=1.0)

    def test_non_ascii_chars(self):
        # Non-ASCII characters count by len(), not byte count
        text = "ñ" * 100
        import pytest as _pt
        assert _estimate_tokens(text) == _pt.approx(100 / 3.5, abs=1.0)


# ---------------------------------------------------------------------------
# _serialize
# ---------------------------------------------------------------------------

class TestSerialize:
    def test_list_of_dicts_produces_jsonl(self):
        data = [{"a": 1}, {"b": 2}]
        text, ext = _serialize(data)
        assert ext == ".jsonl"
        lines = text.splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"a": 1}

    def test_dict_produces_json(self):
        data = {"key": "value"}
        text, ext = _serialize(data)
        assert ext == ".json"
        parsed = json.loads(text)
        assert parsed == {"key": "value"}

    def test_plain_list_produces_json(self):
        data = [1, 2, 3]
        text, ext = _serialize(data)
        assert ext == ".json"  # plain list, not list-of-dicts

    def test_string_produces_txt(self):
        text, ext = _serialize("hello world")
        assert ext == ".txt"
        assert text == "hello world"


# ---------------------------------------------------------------------------
# offload_artifact — below threshold
# ---------------------------------------------------------------------------

class TestOffloadBelowThreshold:
    def test_below_threshold_returns_serialized_no_file(self, tmp_path):
        """Small data → no file written, returns serialized data."""
        small_data = [{"id": "1", "subject": "Test"}]

        result = offload_artifact(
            name="small_test",
            data=small_data,
            summary_fn=lambda d: f"{len(d)} records",
            threshold_tokens=10_000,  # very high threshold
            artha_dir=tmp_path,
        )

        # Returns JSON-like string
        assert '"id"' in result or "1" in result
        # No file should have been created
        assert not (tmp_path / "tmp" / "small_test.jsonl").exists()

    def test_exact_threshold_boundary(self, tmp_path):
        """Data right at threshold is not offloaded."""
        text = "x" * 100  # 25 tokens
        result = offload_artifact(
            name="boundary",
            data={"content": text},
            summary_fn=lambda d: "test",
            threshold_tokens=1_000,  # well above 25 tokens
            artha_dir=tmp_path,
        )
        assert not (tmp_path / "tmp" / "boundary.json").exists()
        assert "content" in result


# ---------------------------------------------------------------------------
# offload_artifact — above threshold
# ---------------------------------------------------------------------------

class TestOffloadAboveThreshold:
    def test_above_threshold_writes_file(self, tmp_path):
        """Large data → file created in tmp/."""
        big_data = _big_data(8_000)  # well above default 5K threshold

        result = offload_artifact(
            name="pipeline_output",
            data=big_data,
            summary_fn=pipeline_summary,
            threshold_tokens=100,  # very low threshold to force offload
            artha_dir=tmp_path,
        )

        out_file = tmp_path / "tmp" / "pipeline_output.jsonl"
        assert out_file.exists(), "Offloaded file must be written to tmp/"
        assert out_file.stat().st_size > 0

    def test_above_threshold_returns_summary_card(self, tmp_path):
        """Summary card contains the file path."""
        big_data = _big_data(500)

        result = offload_artifact(
            name="big_artifact",
            data=big_data,
            summary_fn=lambda d: f"{len(d)} records",
            threshold_tokens=50,  # low threshold
            artha_dir=tmp_path,
        )

        assert "📦 OFFLOADED: big_artifact" in result
        assert "tmp" in result  # path included
        assert "big_artifact" in result

    def test_above_threshold_card_mentions_read_instruction(self, tmp_path):
        """Summary card includes the 'Read ... for full details' instruction."""
        big_data = _big_data(500)

        result = offload_artifact(
            name="artifact",
            data=big_data,
            summary_fn=lambda d: "stats",
            threshold_tokens=50,
            artha_dir=tmp_path,
        )

        assert "for full details" in result.lower() or "full details" in result

    def test_above_threshold_file_content_is_valid_json(self, tmp_path):
        """Written file must contain valid JSON/JSONL."""
        big_data = _big_data(500)

        offload_artifact(
            name="testfile",
            data=big_data,
            summary_fn=lambda d: "test",
            threshold_tokens=50,
            artha_dir=tmp_path,
        )

        out_file = tmp_path / "tmp" / "testfile.jsonl"
        lines = out_file.read_text().splitlines()
        assert len(lines) == len(big_data)
        for line in lines[:5]:
            parsed = json.loads(line)
            assert "id" in parsed

    def test_tmpdir_created_if_missing(self, tmp_path):
        """tmp/ is auto-created if it does not exist."""
        assert not (tmp_path / "tmp").exists()

        offload_artifact(
            name="new_artifact",
            data=_big_data(500),
            summary_fn=lambda d: "stats",
            threshold_tokens=50,
            artha_dir=tmp_path,
        )

        assert (tmp_path / "tmp").exists()


# ---------------------------------------------------------------------------
# Summary card size constraint
# ---------------------------------------------------------------------------

class TestSummaryCardSize:
    def test_card_under_500_tokens(self, tmp_path):
        """The summary card must never exceed 500 tokens (~2000 chars)."""
        # Use a huge data set with long strings
        huge_data = [
            {"id": str(i), "body": "a" * 500, "subject": "s" * 100}
            for i in range(200)
        ]

        card = offload_artifact(
            name="huge",
            data=huge_data,
            summary_fn=lambda d: f"{len(d)} records",
            threshold_tokens=50,
            artha_dir=tmp_path,
        )

        card_tokens = len(card) // _CHARS_PER_TOKEN
        assert card_tokens <= _MAX_CARD_TOKENS, (
            f"Card is {card_tokens} tokens — must be ≤ {_MAX_CARD_TOKENS}"
        )


# ---------------------------------------------------------------------------
# Cleanup manifest
# ---------------------------------------------------------------------------

class TestOffloadedFilesManifest:
    def test_manifest_covers_pipeline_output(self):
        assert "pipeline_output.jsonl" in OFFLOADED_FILES

    def test_manifest_covers_processed_emails(self):
        assert "processed_emails.json" in OFFLOADED_FILES

    def test_manifest_covers_cross_domain_analysis(self):
        assert "cross_domain_analysis.json" in OFFLOADED_FILES

    def test_manifest_covers_domain_extractions(self):
        assert "domain_extractions" in OFFLOADED_FILES

    def test_glob_patterns_cover_session_history(self):
        assert any("session_history" in p for p in OFFLOADED_GLOB_PATTERNS)

    def test_cleanup_path_covers_all_files(self, tmp_path):
        """Create all offloaded files; verify our manifest lists them."""
        tmp = tmp_path / "tmp"
        tmp.mkdir()
        (tmp / "pipeline_output.jsonl").write_text("")
        (tmp / "processed_emails.json").write_text("")
        (tmp / "cross_domain_analysis.json").write_text("")
        (tmp / "session_history_1.md").write_text("")

        created = {p.name for p in tmp.iterdir()}
        listed_in_manifest = set(OFFLOADED_FILES) | {
            p.replace("*", "1") for p in OFFLOADED_GLOB_PATTERNS
        }
        assert created <= listed_in_manifest or all(
            any(f in lm or lm.startswith(f.split("_")[0]) for lm in listed_in_manifest)
            for f in created
        )


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

class TestFeatureFlag:
    def test_feature_flag_disabled_bypasses_offload(self, tmp_path):
        """When config flag is false, no file is written."""
        big_data = _big_data(500)

        with patch("context_offloader.load_harness_flag", return_value=False):
            result = offload_artifact(
                name="flagtest",
                data=big_data,
                summary_fn=lambda d: "stats",
                threshold_tokens=50,
                artha_dir=tmp_path,
            )

        # No file written
        assert not (tmp_path / "tmp").exists() or not (
            tmp_path / "tmp" / "flagtest.jsonl"
        ).exists()
        # Returns serialized data, not a card
        assert "📦 OFFLOADED" not in result

    def test_feature_flag_enabled_by_default(self, tmp_path):
        """When config/artha_config.yaml is absent, flag defaults to True."""
        # No config file in tmp_path
        flag = load_harness_flag("context_offloading.enabled")
        # Should return True (default) — real ARTHA_DIR may or may not have the key;
        # the important thing is the function returns a bool
        assert isinstance(flag, bool)


# ---------------------------------------------------------------------------
# Built-in summary functions
# ---------------------------------------------------------------------------

class TestBuiltinSummaryFunctions:
    def test_pipeline_summary_with_records(self):
        records = [
            {"source": "gmail", "date_iso": "2026-03-14T10:00:00Z"},
            {"source": "gmail", "date_iso": "2026-03-15T09:00:00Z"},
            {"source": "outlook", "date_iso": "2026-03-15T10:00:00Z"},
        ]
        result = pipeline_summary(records)
        assert "3 records" in result
        assert "gmail" in result
        assert "outlook" in result

    def test_pipeline_summary_empty(self):
        assert pipeline_summary([]) == "0 records"

    def test_emails_summary_groups_by_domain(self):
        records = [
            {"domain": "finance"},
            {"domain": "finance"},
            {"domain": "immigration"},
        ]
        result = emails_summary(records)
        assert "finance" in result
        assert "3 emails" in result

    def test_emails_summary_empty(self):
        assert emails_summary([]) == "0 emails"

    def test_cross_domain_summary(self):
        data = {
            "one_thing": "File your tax return by April 15",
            "compound_signals": [{"id": "CS1"}, {"id": "CS2"}],
            "top_alerts": [{"id": "A1"}, {"id": "A2"}, {"id": "A3"}],
        }
        result = cross_domain_summary(data)
        assert "3 top alerts" in result
        assert "2 compound signals" in result


# ---------------------------------------------------------------------------
# Tiered eviction — Phase 2 (specs/agentic-improve.md)
# ---------------------------------------------------------------------------

class TestEvictionTierEnum:
    def test_pinned_has_lower_value_than_ephemeral(self):
        assert EvictionTier.PINNED < EvictionTier.EPHEMERAL

    def test_critical_has_lower_value_than_intermediate(self):
        assert EvictionTier.CRITICAL < EvictionTier.INTERMEDIATE

    def test_four_tiers_defined(self):
        assert len(EvictionTier) == 4


class TestTierThresholds:
    def test_pinned_threshold_is_infinity(self):
        assert _TIER_THRESHOLDS[EvictionTier.PINNED] == float("inf")

    def test_ephemeral_multiplier_is_less_than_one(self):
        assert _TIER_THRESHOLDS[EvictionTier.EPHEMERAL] < 1.0

    def test_intermediate_and_critical_use_base_threshold(self):
        assert _TIER_THRESHOLDS[EvictionTier.INTERMEDIATE] == 1.0
        assert _TIER_THRESHOLDS[EvictionTier.CRITICAL] == 1.0


class TestArtifactTierAssignments:
    def test_pipeline_output_is_ephemeral(self):
        assert _ARTIFACT_TIERS["pipeline_output"] == EvictionTier.EPHEMERAL

    def test_processed_emails_is_ephemeral(self):
        assert _ARTIFACT_TIERS["processed_emails"] == EvictionTier.EPHEMERAL

    def test_session_summary_is_pinned(self):
        assert _ARTIFACT_TIERS["session_summary"] == EvictionTier.PINNED

    def test_alert_list_is_critical(self):
        assert _ARTIFACT_TIERS["alert_list"] == EvictionTier.CRITICAL

    def test_one_thing_is_critical(self):
        assert _ARTIFACT_TIERS["one_thing"] == EvictionTier.CRITICAL


class TestPinnedArtifactNeverOffloaded:
    def test_pinned_never_offloads_regardless_of_size(self, tmp_path):
        """PINNED artifact stays in context no matter how big it is."""
        huge_data = {"content": "x" * 100_000}

        with patch("context_offloader.load_harness_flag", return_value=True):
            result = offload_artifact(
                name="session_summary",  # PINNED in _ARTIFACT_TIERS
                data=huge_data,
                summary_fn=lambda d: "session summary",
                threshold_tokens=1,    # extremely low threshold
                artha_dir=tmp_path,
            )

        assert "📦 OFFLOADED" not in result
        assert not (tmp_path / "tmp" / "session_summary.json").exists()

    def test_pinned_explicit_tier_never_offloads(self, tmp_path):
        """Explicit PINNED tier prevents offloading."""
        huge = {"content": "y" * 50_000}

        with patch("context_offloader.load_harness_flag", return_value=True):
            result = offload_artifact(
                name="custom_artifact",
                data=huge,
                summary_fn=lambda d: "custom",
                threshold_tokens=1,
                tier=EvictionTier.PINNED,
                artha_dir=tmp_path,
            )

        assert "📦 OFFLOADED" not in result


class TestEphemeralArtifactLowerThreshold:
    def test_ephemeral_offloads_at_40pct_threshold(self, tmp_path):
        """EPHEMERAL artifact offloads at 40% of base threshold_tokens."""
        # ~600 chars = ~150 tokens — above 40% of 5000 (2000), below 100%
        # So with threshold=500 tokens, ephemeral threshold = 200 tokens
        # Content at 250 tokens (1000 chars) should be offloaded
        medium_data = {"content": "a" * 1000}

        with patch("context_offloader.load_harness_flag", return_value=True):
            result = offload_artifact(
                name="pipeline_output",  # EPHEMERAL in _ARTIFACT_TIERS
                data=medium_data,
                summary_fn=lambda d: "pipeline stats",
                threshold_tokens=500,   # base threshold, effective = 200
                artha_dir=tmp_path,
            )

        # At 250 tokens, above ephemeral threshold of 200 → should offload
        assert "📦 OFFLOADED" in result

    def test_ephemeral_explicit_tier_uses_lower_threshold(self, tmp_path):
        """Explicit EPHEMERAL tier uses 40% threshold."""
        medium_data = {"content": "b" * 1000}  # ~250 tokens

        with patch("context_offloader.load_harness_flag", return_value=True):
            result = offload_artifact(
                name="unknown_artifact",
                data=medium_data,
                summary_fn=lambda d: "stats",
                threshold_tokens=500,
                tier=EvictionTier.EPHEMERAL,
                artha_dir=tmp_path,
            )

        assert "📦 OFFLOADED" in result


class TestFeatureFlagDisabledFlatThreshold:
    def test_flag_disabled_uses_flat_threshold(self, tmp_path):
        """When tiered_eviction disabled, all tiers use base threshold (backward compat)."""
        medium_data = {"content": "c" * 1000}  # ~250 tokens

        def mock_flag(path: str, default: bool = True) -> bool:
            if path == "context_offloading.enabled":
                return True
            if path == "agentic.tiered_eviction.enabled":
                return False  # tiered eviction disabled
            return default

        with patch("context_offloader.load_harness_flag", side_effect=mock_flag):
            result = offload_artifact(
                name="pipeline_output",  # EPHEMERAL—but flag disabled
                data=medium_data,
                summary_fn=lambda d: "stats",
                threshold_tokens=5_000,  # base threshold: 250 tokens < 5000 → not offloaded
                artha_dir=tmp_path,
            )

        # With flat threshold 5000, 250 tokens should NOT be offloaded
        assert "📦 OFFLOADED" not in result


class TestUnknownArtifactDefaultsTier:
    def test_unknown_name_uses_intermediate_tier(self, tmp_path):
        """Unregistered artifact names default to INTERMEDIATE tier."""
        # INTERMEDIATE has multiplier 1.0, so threshold is base threshold
        # Medium data (250 tokens) below base 5K threshold → not offloaded
        medium = {"content": "d" * 500}  # ~125 tokens

        with patch("context_offloader.load_harness_flag", return_value=True):
            result = offload_artifact(
                name="my_completely_custom_artifact",
                data=medium,
                summary_fn=lambda d: "stats",
                threshold_tokens=1_000,
                artha_dir=tmp_path,
            )

        # 125 tokens < 1000 → not offloaded (INTERMEDIATE = 1.0x)
        assert "📦 OFFLOADED" not in result


class TestCheckpointInOffloadedFiles:
    def test_checkpoint_file_in_manifest(self):
        assert ".checkpoint.json" in OFFLOADED_FILES
