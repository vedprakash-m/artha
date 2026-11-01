"""
tests/unit/test_middleware_step_hooks.py — Unit tests for AFW-3 step hooks.

Coverage:
  - _ComposedMiddleware orchestration: run_before_step / run_after_step / run_on_error
  - best-effort behaviour: one hook crash doesn't prevent others from running
  - All 5 existing middleware classes implement before_step/after_step/on_error as no-ops
  - isinstance checks against StateMiddleware still pass for all 5

Ref: specs/agent-fw.md §3.3 (AFW-3)
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from middleware import (
    StateMiddleware,
    _ComposedMiddleware,
    _PassthroughMiddleware,
)
from middleware.audit_middleware import AuditMiddleware
from middleware.pii_middleware import PIIMiddleware
from middleware.rate_limiter import RateLimiter
from middleware.write_guard import WriteGuardMiddleware
from middleware.write_verify import WriteVerifyMiddleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mw(before_raises=False, after_raises=False, on_error_raises=False):
    """Build a mock middleware that optionally raises on any hook."""
    mw = MagicMock(spec=StateMiddleware)
    if before_raises:
        mw.before_step.side_effect = RuntimeError("before crash")
    if after_raises:
        mw.after_step.side_effect = RuntimeError("after crash")
    if on_error_raises:
        mw.on_error.side_effect = RuntimeError("on_error crash")
    return mw


def _composed(*inner) -> _ComposedMiddleware:
    c = _ComposedMiddleware.__new__(_ComposedMiddleware)
    c._middlewares = list(inner)
    return c


# ---------------------------------------------------------------------------
# T-SH1: _PassthroughMiddleware has no-ops
# ---------------------------------------------------------------------------

class TestPassthroughMiddleware:
    def test_before_step_is_callable(self):
        mw = _PassthroughMiddleware()
        mw.before_step("step1", {}, None)  # must not raise

    def test_after_step_is_callable(self):
        mw = _PassthroughMiddleware()
        mw.after_step("step1", {}, "result")

    def test_on_error_is_callable(self):
        mw = _PassthroughMiddleware()
        mw.on_error("step1", {}, ValueError("fail"))

    def test_isinstance_state_middleware(self):
        assert isinstance(_PassthroughMiddleware(), StateMiddleware)


# ---------------------------------------------------------------------------
# T-SH2: _ComposedMiddleware — before_step
# ---------------------------------------------------------------------------

class TestComposedBeforeStep:
    def test_calls_all_middlewares(self):
        m1, m2, m3 = _make_mw(), _make_mw(), _make_mw()
        c = _composed(m1, m2, m3)
        c.run_before_step("step_x", {"k": "v"}, "input_data")
        m1.before_step.assert_called_once_with("step_x", {"k": "v"}, "input_data")
        m2.before_step.assert_called_once_with("step_x", {"k": "v"}, "input_data")
        m3.before_step.assert_called_once_with("step_x", {"k": "v"}, "input_data")

    def test_crash_in_one_does_not_stop_others(self):
        m1 = _make_mw(before_raises=True)
        m2 = _make_mw()
        m3 = _make_mw()
        c = _composed(m1, m2, m3)
        c.run_before_step("step_x", {}, None)  # must NOT raise
        m2.before_step.assert_called_once()
        m3.before_step.assert_called_once()

    def test_empty_composed_does_not_raise(self):
        c = _composed()
        c.run_before_step("step_x", {}, None)


# ---------------------------------------------------------------------------
# T-SH3: _ComposedMiddleware — after_step
# ---------------------------------------------------------------------------

class TestComposedAfterStep:
    def test_calls_all_middlewares(self):
        m1, m2 = _make_mw(), _make_mw()
        c = _composed(m1, m2)
        c.run_after_step("step_y", {"k": "v"}, "output_data")
        m1.after_step.assert_called_once_with("step_y", {"k": "v"}, "output_data")
        m2.after_step.assert_called_once_with("step_y", {"k": "v"}, "output_data")

    def test_crash_in_one_does_not_stop_others(self):
        m1 = _make_mw(after_raises=True)
        m2 = _make_mw()
        c = _composed(m1, m2)
        c.run_after_step("step_y", {}, None)
        m2.after_step.assert_called_once()


# ---------------------------------------------------------------------------
# T-SH4: _ComposedMiddleware — on_error
# ---------------------------------------------------------------------------

class TestComposedOnError:
    def test_calls_all_middlewares(self):
        m1, m2 = _make_mw(), _make_mw()
        c = _composed(m1, m2)
        err = ValueError("boom")
        c.run_on_error("step_z", {"k": "v"}, err)
        m1.on_error.assert_called_once_with("step_z", {"k": "v"}, err)
        m2.on_error.assert_called_once_with("step_z", {"k": "v"}, err)

    def test_crash_in_one_does_not_stop_others(self):
        m1 = _make_mw(on_error_raises=True)
        m2 = _make_mw()
        err = RuntimeError("pipeline error")
        c = _composed(m1, m2)
        c.run_on_error("step_z", {}, err)
        m2.on_error.assert_called_once()


# ---------------------------------------------------------------------------
# T-SH5: All 5 existing middleware implement the new hooks as no-ops
# ---------------------------------------------------------------------------

class TestExistingMiddlewareNoOps:
    @pytest.fixture(autouse=True)
    def _tmp_dir(self, tmp_path, monkeypatch):
        """Provide a temp dir for any middleware that needs a writable path."""
        self.tmp = tmp_path

    def _check_noop_hooks(self, mw):
        mw.before_step("step", {}, None)         # no raise
        mw.after_step("step", {}, None)          # no raise
        mw.on_error("step", {}, Exception("e"))  # no raise

    def test_write_guard_noop_hooks(self):
        mw = WriteGuardMiddleware()
        self._check_noop_hooks(mw)

    def test_pii_middleware_noop_hooks(self, monkeypatch):
        monkeypatch.setattr(
            "middleware.pii_middleware.PIIMiddleware.__init__",
            lambda self: None,
        )
        mw = PIIMiddleware.__new__(PIIMiddleware)
        self._check_noop_hooks(mw)

    def test_rate_limiter_noop_hooks(self, monkeypatch):
        monkeypatch.setattr(
            "middleware.rate_limiter.RateLimiter.__init__",
            lambda self: None,
        )
        mw = RateLimiter.__new__(RateLimiter)
        self._check_noop_hooks(mw)

    def test_audit_middleware_noop_hooks(self, monkeypatch):
        monkeypatch.setattr(
            "middleware.audit_middleware.AuditMiddleware.__init__",
            lambda self: None,
        )
        mw = AuditMiddleware.__new__(AuditMiddleware)
        self._check_noop_hooks(mw)

    def test_write_verify_noop_hooks(self, monkeypatch):
        monkeypatch.setattr(
            "middleware.write_verify.WriteVerifyMiddleware.__init__",
            lambda self: None,
        )
        mw = WriteVerifyMiddleware.__new__(WriteVerifyMiddleware)
        self._check_noop_hooks(mw)


# ---------------------------------------------------------------------------
# T-SH6: isinstance checks against StateMiddleware (protocol compat)
# ---------------------------------------------------------------------------

class TestStateMiddlewareProtocolCompat:
    @pytest.mark.parametrize(
        "cls",
        [WriteGuardMiddleware, PIIMiddleware, RateLimiter, AuditMiddleware, WriteVerifyMiddleware],
    )
    def test_existing_middleware_is_state_middleware(self, cls, monkeypatch):
        # Patch __init__ to avoid complex setup
        monkeypatch.setattr(cls, "__init__", lambda self: None)
        instance = cls.__new__(cls)
        assert isinstance(instance, StateMiddleware), (
            f"{cls.__name__} is not instanceof StateMiddleware after AFW-3 no-ops"
        )
