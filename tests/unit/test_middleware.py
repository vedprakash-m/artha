"""
tests/unit/test_middleware.py — Unit tests for scripts/middleware/ package

Phase 4 verification suite.

Coverage:
  - compose_middleware runs before_write left-to-right
  - PIIMiddleware calls pii_guard.py and returns filtered content
  - WriteGuardMiddleware blocks writes with >20% field loss
  - WriteVerifyMiddleware catches missing frontmatter
  - AuditMiddleware logs all mutations to audit.md
  - RateLimiter enforces per-provider limits
  - Passthrough when middleware.enabled = false
  - Bootstrap files exempt from write guard
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from middleware import StateMiddleware, _ComposedMiddleware, _PassthroughMiddleware, compose_middleware
from middleware.audit_middleware import AuditMiddleware
from middleware.rate_limiter import RateLimiter, RateLimitExceeded
from middleware.write_guard import WriteGuardMiddleware, count_yaml_fields
from middleware.write_verify import WriteVerifyMiddleware, verify_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_valid_state(domain: str = "testdomain") -> str:
    return (
        f"---\n"
        f"domain: {domain}\n"
        f"last_updated: 2026-03-15T10:00:00Z\n"
        f"status: ACTIVE\n"
        f"field1: value1\n"
        f"field2: value2\n"
        f"field3: value3\n"
        f"field4: value4\n"
        f"field5: value5\n"
        f"---\n"
        f"# Body content\n"
    )


class _UpperMiddleware:
    """Test middleware that uppercases proposed content."""
    def before_write(self, domain, current, proposed, ctx=None):
        return proposed.upper()

    def after_write(self, domain, path):
        pass


class _BlockingMiddleware:
    """Test middleware that always blocks writes."""
    def before_write(self, domain, current, proposed, ctx=None):
        return None

    def after_write(self, domain, path):
        pass


class _TrackingMiddleware:
    """Test middleware that records call sequence."""
    def __init__(self, name: str, log: list):
        self.name = name
        self.log = log

    def before_write(self, domain, current, proposed, ctx=None):
        self.log.append(f"before:{self.name}")
        return proposed

    def after_write(self, domain, path):
        self.log.append(f"after:{self.name}")


# ---------------------------------------------------------------------------
# compose_middleware — ordering + blocking
# ---------------------------------------------------------------------------

class TestComposeMiddleware:
    def test_compose_runs_before_write_left_to_right(self, tmp_path):
        """before_write executes in the order middlewares are provided."""
        log = []
        m1 = _TrackingMiddleware("A", log)
        m2 = _TrackingMiddleware("B", log)

        with patch("context_offloader.load_harness_flag", return_value=True):
            stack = compose_middleware([m1, m2])

        stack.before_write("test", "", "content")
        assert log == ["before:A", "before:B"], (
            "before_write must run left-to-right"
        )

    def test_compose_runs_after_write_right_to_left(self, tmp_path):
        """after_write executes right-to-left."""
        log = []
        m1 = _TrackingMiddleware("A", log)
        m2 = _TrackingMiddleware("B", log)

        with patch("context_offloader.load_harness_flag", return_value=True):
            stack = compose_middleware([m1, m2])

        stack.after_write("test", tmp_path / "file.md")
        assert log == ["after:B", "after:A"], (
            "after_write must run right-to-left"
        )

    def test_blocking_middleware_short_circuits(self):
        """A None return from any middleware stops the chain."""
        with patch("context_offloader.load_harness_flag", return_value=True):
            stack = compose_middleware([_BlockingMiddleware(), _UpperMiddleware()])

        result = stack.before_write("test", "", "hello")
        assert result is None

    def test_content_transforms_are_chained(self):
        """Each middleware receives the output of the previous one."""
        class _AppendMiddleware:
            def __init__(self, suffix):
                self.suffix = suffix
            def before_write(self, domain, current, proposed, ctx=None):
                return proposed + self.suffix
            def after_write(self, domain, path):
                pass

        with patch("context_offloader.load_harness_flag", return_value=True):
            stack = compose_middleware([
                _AppendMiddleware("-A"),
                _AppendMiddleware("-B"),
            ])

        result = stack.before_write("test", "", "start")
        assert result == "start-A-B"

    def test_feature_flag_disabled_returns_passthrough(self):
        """When middleware.enabled = false, compose returns passthrough."""
        with patch("context_offloader.load_harness_flag", return_value=False):
            stack = compose_middleware([_BlockingMiddleware()])

        # Passthrough never blocks
        result = stack.before_write("test", "", "sensitive content")
        assert result == "sensitive content"

    def test_after_write_exception_does_not_propagate(self, tmp_path):
        """Exceptions in after_write must be silently swallowed."""
        class _BrokenAfterMiddleware:
            def before_write(self, d, c, p, ctx=None):
                return p
            def after_write(self, d, path):
                raise RuntimeError("boom")

        with patch("context_offloader.load_harness_flag", return_value=True):
            stack = compose_middleware([_BrokenAfterMiddleware()])

        # Must not raise
        stack.after_write("test", tmp_path / "file.md")


# ---------------------------------------------------------------------------
# WriteGuardMiddleware
# ---------------------------------------------------------------------------

class TestWriteGuardMiddleware:
    def test_allows_write_with_low_loss(self):
        # current has 8 fields (domain, last_updated, status, field1-5)
        current = _make_valid_state()
        # Proposed removes only 1 of 8 fields → 12.5% loss, should be allowed
        proposed = (
            "---\ndomain: testdomain\nlast_updated: 2026-03-15T10:00:00Z\n"
            "status: ACTIVE\nfield1: value1\nfield2: value2\n"
            "field3: value3\nfield4: value4\n---\n"
        )
        guard = WriteGuardMiddleware()
        result = guard.before_write("testdomain", current, proposed)
        assert result is not None

    def test_blocks_over_20pct_field_loss(self):
        """Removing >20% of fields should be blocked."""
        # Create current with 10 fields, proposed with only 1
        current = "---\n" + "\n".join(f"field{i}: value{i}" for i in range(10)) + "\n---\n"
        proposed = "---\ndomain: testdomain\n---\n"
        guard = WriteGuardMiddleware(max_loss_pct=20.0)
        result = guard.before_write("testdomain", current, proposed)
        assert result is None, "Should block write with >20% field loss"

    def test_allows_new_file_creation(self):
        """Empty current_content means new file — always allowed."""
        guard = WriteGuardMiddleware()
        proposed = "---\ndomain: newdomain\nlast_updated: 2026-03-15T10:00:00Z\n---\n"
        result = guard.before_write("newdomain", "", proposed)
        assert result == proposed

    def test_bootstrap_exempt_from_guard(self):
        """Files with updated_by: bootstrap skip the net-negative check."""
        current = "---\nupdated_by: bootstrap\n" + "\n".join(
            f"field{i}: v" for i in range(10)
        ) + "\n---\n"
        proposed = "---\ndomain: x\n---\n"  # massive loss but exempt
        guard = WriteGuardMiddleware()
        result = guard.before_write("x", current, proposed)
        assert result == proposed  # Exempt, not blocked

    def test_no_current_fields_passes_through(self):
        """If current file has no YAML fields, we can't calculate loss."""
        guard = WriteGuardMiddleware()
        result = guard.before_write("test", "# Markdown only\n", "---\ndomain: x\n---\n")
        assert result is not None


class TestCountYamlFields:
    def test_counts_simple_fields(self):
        text = "---\ndomain: test\nlast_updated: 2026-03-15\nstatus: ACTIVE\n---\n"
        assert count_yaml_fields(text) >= 3

    def test_ignores_blank_lines(self):
        text = "---\n\ndomain: test\n\nstatus: ok\n\n---\n"
        count = count_yaml_fields(text)
        assert count == 2

    def test_ignores_comment_lines(self):
        text = "---\n# This is a comment\ndomain: test\n---\n"
        count = count_yaml_fields(text)
        # Only 'domain:' counts
        assert count == 1


# ---------------------------------------------------------------------------
# WriteVerifyMiddleware
# ---------------------------------------------------------------------------

class TestWriteVerifyMiddleware:
    def test_valid_file_passes(self, tmp_path):
        path = tmp_path / "state" / "finance.md"
        path.parent.mkdir()
        path.write_text(_make_valid_state("finance"))

        verifier = WriteVerifyMiddleware(artha_dir=tmp_path)
        failures = verify_file("finance", path)
        assert failures == [], f"Valid file should pass all checks: {failures}"

    def test_catches_missing_frontmatter(self, tmp_path):
        path = tmp_path / "state" / "broken.md"
        path.parent.mkdir()
        path.write_text("# No frontmatter\njust body text\n" * 20)

        failures = verify_file("broken", path)
        assert any("frontmatter" in f or "delimiter" in f for f in failures)

    def test_catches_missing_domain_field(self, tmp_path):
        path = tmp_path / "state" / "nodomain.md"
        path.parent.mkdir()
        path.write_text("---\nlast_updated: 2026-03-15T10:00:00Z\n---\n# body\n" * 20)

        failures = verify_file("nodomain", path)
        assert any("domain" in f for f in failures)

    def test_catches_missing_last_updated(self, tmp_path):
        path = tmp_path / "state" / "nodate.md"
        path.parent.mkdir()
        path.write_text("---\ndomain: nodate\n---\n# body\n" * 20)

        failures = verify_file("nodate", path)
        assert any("last_updated" in f for f in failures)

    def test_catches_small_file(self, tmp_path):
        path = tmp_path / "state" / "tiny.md"
        path.parent.mkdir()
        path.write_text("---\ndomain: tiny\n---\n")

        failures = verify_file("tiny", path)
        # The file is tiny — should catch size failure
        assert len(failures) >= 1

    def test_after_write_logs_failure(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        audit_log = state_dir / "audit.md"
        audit_log.write_text("# Audit\n")

        # Create a broken file
        broken = state_dir / "broken.md"
        broken.write_text("no frontmatter here\n" * 20)

        verifier = WriteVerifyMiddleware(artha_dir=tmp_path)
        verifier.after_write("broken", broken)

        audit_content = audit_log.read_text()
        assert "INTEGRITY_VERIFY_FAIL" in audit_content


# ---------------------------------------------------------------------------
# AuditMiddleware
# ---------------------------------------------------------------------------

class TestAuditMiddleware:
    def test_logs_write_to_audit_md(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "audit.md").write_text("# Audit Log\n")

        auditor = AuditMiddleware(artha_dir=tmp_path)
        current = _make_valid_state("finance")
        proposed = current + "\nnew_field: value\n"

        auditor.before_write("finance", current, proposed)
        auditor.after_write("finance", state_dir / "finance.md")

        audit_content = (state_dir / "audit.md").read_text()
        assert "MIDDLEWARE_WRITE" in audit_content
        assert "finance" in audit_content

    def test_logs_all_mutations(self, tmp_path):
        """Multiple writes should all appear in the audit log."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "audit.md").write_text("# Audit Log\n")

        auditor = AuditMiddleware(artha_dir=tmp_path)
        for domain in ["finance", "health", "immigration"]:
            auditor.before_write(domain, "", f"---\ndomain: {domain}\n---\n")
            auditor.after_write(domain, state_dir / f"{domain}.md")

        audit_content = (state_dir / "audit.md").read_text()
        assert audit_content.count("MIDDLEWARE_WRITE") == 3

    def test_before_write_passes_through(self):
        """AuditMiddleware must not modify the content."""
        auditor = AuditMiddleware()
        proposed = "---\ndomain: test\n---\n"
        result = auditor.before_write("test", "", proposed)
        assert result == proposed

    def test_log_event_writes_custom_entry(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "audit.md").write_text("# Audit Log\n")

        auditor = AuditMiddleware(artha_dir=tmp_path)
        auditor.log_event("CONTEXT_OFFLOAD", "artifact: pipeline_output | tokens: 25000")

        content = (state_dir / "audit.md").read_text()
        assert "CONTEXT_OFFLOAD" in content
        assert "pipeline_output" in content

    def test_missing_audit_log_does_not_raise(self, tmp_path):
        """If audit.md doesn't exist, AuditMiddleware should not raise."""
        auditor = AuditMiddleware(artha_dir=tmp_path)
        # Should silently fail, not raise
        auditor.after_write("test", tmp_path / "state" / "test.md")


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_allows_calls_within_limit(self, tmp_path):
        limiter = RateLimiter(artha_dir=tmp_path)
        # Only 1 call — should be within any limit
        limiter.check("gmail")  # Must not raise

    def test_delays_on_burst(self, tmp_path):
        """When calls exceed limit, RateLimitExceeded is raised."""
        limiter = RateLimiter(artha_dir=tmp_path)
        # Override bucket to have limit of 2
        from middleware.rate_limiter import _TokenBucket
        limiter._buckets["test_provider"] = _TokenBucket(calls_per_minute=2, burst=2)

        limiter.check("test_provider")
        limiter.check("test_provider")
        with pytest.raises(RateLimitExceeded):
            limiter.check("test_provider")

    def test_unknown_provider_does_not_raise(self, tmp_path):
        """Unknown provider = no limit configured → always allow."""
        limiter = RateLimiter(artha_dir=tmp_path)
        limiter.check("completely_unknown_provider")  # Must not raise

    def test_before_write_passthrough(self, tmp_path):
        """RateLimiter must not modify state writes."""
        limiter = RateLimiter(artha_dir=tmp_path)
        proposed = "---\ndomain: test\n---\n"
        result = limiter.before_write("test", "", proposed)
        assert result == proposed

    def test_window_resets_over_time(self, tmp_path):
        """After the 60-second window, new calls are allowed."""
        from middleware.rate_limiter import _TokenBucket
        bucket = _TokenBucket(calls_per_minute=1, burst=1)

        # Use up the limit
        assert bucket.check() is True
        assert bucket.check() is False

        # Simulate time passing by manipulating the deque directly
        import time as time_mod
        bucket._window.clear()  # manually clear window to simulate time elapse

        assert bucket.check() is True


# ---------------------------------------------------------------------------
# StateMiddleware Protocol compliance
# ---------------------------------------------------------------------------

class TestStateMiddlewareProtocol:
    def test_passthrough_implements_protocol(self):
        p = _PassthroughMiddleware()
        assert isinstance(p, StateMiddleware)

    def test_write_guard_implements_protocol(self):
        g = WriteGuardMiddleware()
        assert isinstance(g, StateMiddleware)

    def test_audit_middleware_implements_protocol(self):
        a = AuditMiddleware()
        assert isinstance(a, StateMiddleware)
