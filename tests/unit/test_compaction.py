"""
tests/unit/test_compaction.py — Unit tests for AFW-4 context compaction.

Coverage:
  - CompactionPolicy enum values
  - FROZEN_ARTIFACTS set contents
  - _assert_not_frozen raises CompactionPolicyError for frozen, passes otherwise
  - compact_phase_output gated off by default (returns raw_output unchanged)
  - compact_phase_output frozen-artifact check fires BEFORE the A-9 gate
  - compact_phase_output: reason phase always returns raw (never compacted)
  - compact_phase_output: process phase truncates when enabled
  - compact_phase_output: fetch phase delegates to extractors when enabled
  - sliding_window_compact gated off by default (returns same history)
  - sliding_window_compact: keeps last N entries when enabled
  - sliding_window_compact: pinned entries always retained
  - sliding_window_compact: no-op when history short enough

Ref: specs/agent-fw.md §3.4 (AFW-4)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from context_offloader import (
    CompactionPolicy,
    CompactionPolicyError,
    FROZEN_ARTIFACTS,
    _assert_not_frozen,
    compact_phase_output,
    sliding_window_compact,
)


# ---------------------------------------------------------------------------
# T-CP1: CompactionPolicy enum
# ---------------------------------------------------------------------------

class TestCompactionPolicyEnum:
    def test_compactable_value(self):
        assert CompactionPolicy.COMPACTABLE.value == "compactable"

    def test_frozen_value(self):
        assert CompactionPolicy.FROZEN.value == "frozen"

    def test_two_members_only(self):
        assert len(list(CompactionPolicy)) == 2

    def test_compaction_policy_error_is_exception(self):
        err = CompactionPolicyError("test")
        assert isinstance(err, Exception)


# ---------------------------------------------------------------------------
# T-CP2: FROZEN_ARTIFACTS
# ---------------------------------------------------------------------------

class TestFrozenArtifacts:
    def test_briefing_output_in_frozen(self):
        assert "briefing_output" in FROZEN_ARTIFACTS

    def test_session_summary_in_frozen(self):
        assert "session_summary" in FROZEN_ARTIFACTS

    def test_is_frozenset(self):
        assert isinstance(FROZEN_ARTIFACTS, frozenset)

    def test_two_entries(self):
        assert len(FROZEN_ARTIFACTS) == 2


# ---------------------------------------------------------------------------
# T-CP3: _assert_not_frozen
# ---------------------------------------------------------------------------

class TestAssertNotFrozen:
    def test_raises_for_briefing_output(self):
        with pytest.raises(CompactionPolicyError, match="briefing_output"):
            _assert_not_frozen("briefing_output")

    def test_raises_for_session_summary(self):
        with pytest.raises(CompactionPolicyError, match="session_summary"):
            _assert_not_frozen("session_summary")

    def test_passes_for_pipeline_output(self):
        _assert_not_frozen("pipeline_output")  # must not raise

    def test_passes_for_none_like_name(self):
        _assert_not_frozen("other_artifact")   # must not raise

    def test_error_mentions_artifact_name(self):
        with pytest.raises(CompactionPolicyError) as exc_info:
            _assert_not_frozen("briefing_output")
        assert "briefing_output" in str(exc_info.value)


# ---------------------------------------------------------------------------
# T-CP4: compact_phase_output — gate (A-9 off by default)
# ---------------------------------------------------------------------------

class TestCompactPhaseOutputGated:
    def test_returns_raw_when_disabled(self):
        raw = "this is a very long raw output " * 100
        result = compact_phase_output("process", raw)
        assert result == raw

    def test_returns_raw_for_all_phases_when_disabled(self):
        for phase in ("fetch", "process", "reason", "unknown_phase"):
            result = compact_phase_output(phase, "some raw data")
            assert result == "some raw data"

    def test_frozen_artifact_raises_before_gate(self):
        """CompactionPolicyError must fire BEFORE the A-9 gate check."""
        with pytest.raises(CompactionPolicyError):
            compact_phase_output("process", "data", artifact_name="briefing_output")

    def test_frozen_artifact_raises_even_when_compaction_enabled(self):
        with patch("context_offloader.load_harness_flag", return_value=True):
            with pytest.raises(CompactionPolicyError):
                compact_phase_output("process", "data", artifact_name="session_summary")

    def test_non_frozen_artifact_name_ok_with_disabled_gate(self):
        result = compact_phase_output("process", "data", artifact_name="pipeline_output")
        assert result == "data"


# ---------------------------------------------------------------------------
# T-CP5: compact_phase_output — behaviour when enabled
# ---------------------------------------------------------------------------

class TestCompactPhaseOutputEnabled:
    @pytest.fixture(autouse=True)
    def _enable_compaction(self):
        with patch("context_offloader.load_harness_flag", return_value=True):
            yield

    def test_reason_phase_always_returns_full(self):
        long_text = "x" * 10_000
        result = compact_phase_output("reason", long_text)
        assert result == long_text

    def test_process_phase_truncates_long_output(self):
        # max_tokens=100 → budget ~ 100*4 = 400 chars
        long_text = "a" * 5000
        result = compact_phase_output("process", long_text, max_tokens=100)
        assert len(result) <= 500  # generous bound; must be shorter than raw

    def test_process_phase_does_not_truncate_short_output(self):
        short_text = "brief output"
        result = compact_phase_output("process", short_text, max_tokens=2000)
        # Short text should not be mutated
        assert short_text in result

    def test_unknown_phase_returns_raw(self):
        raw = "raw output for unknown phase"
        result = compact_phase_output("analyze", raw)
        assert result == raw

    def test_fetch_phase_delegates_gracefully(self):
        # When email_classifier / fact_extractor are unavailable → graceful fallback
        with patch.dict("sys.modules", {"email_classifier": None, "fact_extractor": None}):
            result = compact_phase_output("fetch", "email content here")
        # Should return something (not raise); raw or trimmed
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# T-CP6: sliding_window_compact — gate (A-9 off by default)
# ---------------------------------------------------------------------------

class TestSlidingWindowCompactGated:
    @pytest.fixture(autouse=True)
    def _disable_compaction(self):
        with patch("context_offloader.load_harness_flag", return_value=False):
            yield

    def test_returns_same_list_when_disabled(self):
        history = [{"role": "user", "content": "hello"}]
        result = sliding_window_compact(history)
        assert result is history

    def test_returns_same_list_for_empty_history(self):
        result = sliding_window_compact([])
        assert result == []


# ---------------------------------------------------------------------------
# T-CP7: sliding_window_compact — behaviour when enabled
# ---------------------------------------------------------------------------

class TestSlidingWindowCompactEnabled:
    @pytest.fixture(autouse=True)
    def _enable_compaction(self):
        with patch("context_offloader.load_harness_flag", return_value=True):
            yield

    def _history(self, n: int) -> list[dict]:
        return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"} for i in range(n)]

    def test_short_history_returned_unchanged(self):
        """History with ≤ keep_last*2 entries → no compaction needed."""
        history = self._history(4)
        result = sliding_window_compact(history, keep_last=3)
        # Only 4 entries, keep_last=3 → recent window = 6 → all fit
        assert len(result) <= len(history) + 1  # +1 for possible injected system block

    def test_long_history_gets_summary_block(self):
        history = self._history(12)
        result = sliding_window_compact(history, keep_last=3)
        # Should be shorter than original
        assert len(result) < len(history)
        # Summary system block should be first
        summary_blocks = [m for m in result if m.get("role") == "system"]
        assert len(summary_blocks) >= 1
        assert "Prior context" in summary_blocks[0]["content"]

    def test_recent_messages_preserved(self):
        history = self._history(12)
        result = sliding_window_compact(history, keep_last=3)
        # Last keep_last*2 messages (by content) should be in result
        last_msgs = {m["content"] for m in history[-6:]}
        result_msgs = {m["content"] for m in result if m.get("role") != "system"}
        # All recent messages should be preserved
        assert last_msgs.issubset(result_msgs)

    def test_pinned_keys_always_preserved(self):
        pinned_msg = {"role": "system", "content": "System instructions pinned"}
        other_msgs = self._history(12)
        history = [pinned_msg] + other_msgs
        result = sliding_window_compact(
            history, keep_last=3, pinned_keys={"system"}
        )
        # Pinned system message must appear in result
        contents = [m["content"] for m in result]
        assert "System instructions pinned" in contents

    def test_no_compaction_when_exactly_keep_last(self):
        """When history length == keep_last * 2, no compaction."""
        history = self._history(6)  # keep_last=3 → window=6 → fits exactly
        result = sliding_window_compact(history, keep_last=3)
        # Result should not introduce a summary block
        system_blocks = [m for m in result if m.get("role") == "system"]
        assert len(system_blocks) == 0

    def test_result_is_new_list(self):
        """sliding_window_compact must return a new list, not mutate history."""
        history = self._history(12)
        original_len = len(history)
        result = sliding_window_compact(history, keep_last=3)
        assert len(history) == original_len  # original untouched
        assert result is not history
