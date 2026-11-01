#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/middleware/rate_limiter.py — API rate limiting middleware.

Enforces per-provider API rate limits defined in config/connectors.yaml.  This
is a new capability not previously present in the inline workflow, and it
implements the middleware-layer rate limiting described in the Deep Agents spec.

Usage:
    from middleware.rate_limiter import RateLimiter

    limiter = RateLimiter()
    limiter.check("gmail")   # raises RateLimitExceeded if limit reached

    # As a StateMiddleware (does not restrict state writes — only API calls):
    from middleware import compose_middleware
    stack = compose_middleware([RateLimiter()])
    # Note: RateLimiter.before_write always passes through — it guards API
    # calls, not state writes.  Use check() for API call-sites.

Config in connectors.yaml (per-connector, optional):
    gmail:
      rate_limit:
        calls_per_minute: 30
        burst: 10

Ref: specs/deep-agents.md Phase 4
"""
from __future__ import annotations

import time
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Any


class RateLimitExceeded(Exception):
    """Raised when an API provider's rate limit is exceeded."""

    def __init__(self, provider: str, calls_per_minute: int) -> None:
        super().__init__(
            f"Rate limit exceeded for {provider}: "
            f"max {calls_per_minute} calls/minute"
        )
        self.provider = provider
        self.calls_per_minute = calls_per_minute


class _TokenBucket:
    """Sliding-window rate limiter for a single API provider.

    Uses a deque of timestamps to implement a sliding 60-second window.
    Thread-safe via a per-bucket lock.
    """

    def __init__(self, calls_per_minute: int, burst: int) -> None:
        self.calls_per_minute = calls_per_minute
        self.burst = burst
        self._window: deque[float] = deque()
        self._lock = Lock()

    def check(self) -> bool:
        """Return True if a call is allowed, False if the limit is reached."""
        now = time.monotonic()
        window_start = now - 60.0

        with self._lock:
            # Purge timestamps outside the 60-second window
            while self._window and self._window[0] < window_start:
                self._window.popleft()

            if len(self._window) >= self.calls_per_minute:
                return False

            self._window.append(now)
            return True


class RateLimiter:
    """Per-provider API rate limiter, loadable from connectors.yaml.

    As a StateMiddleware: before_write always returns proposed_content
    unchanged — this class does not restrict state file writes.  Its
    rate-limiting function is only invoked via ``check(provider)``.
    """

    # Default limits used when connectors.yaml has no rate_limit section
    _DEFAULTS: dict[str, dict[str, int]] = {
        "gmail": {"calls_per_minute": 30, "burst": 10},
        "msgraph": {"calls_per_minute": 20, "burst": 5},
        "icloud": {"calls_per_minute": 10, "burst": 3},
    }

    def __init__(self, artha_dir: Path | None = None) -> None:
        from lib.common import ARTHA_DIR  # noqa: PLC0415

        self._artha_dir = artha_dir or ARTHA_DIR
        self._buckets: dict[str, _TokenBucket] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load rate limit settings from config/connectors.yaml."""
        try:
            from lib.config_loader import load_config  # noqa: PLC0415

            cfg = load_config("connectors", str(self._artha_dir / "config"))
            connectors: list[dict[str, Any]] = cfg.get("connectors", [])
            for connector in connectors:
                name = connector.get("name", connector.get("id", ""))
                rl = connector.get("rate_limit", {})
                if rl and name:
                    cpm = int(rl.get("calls_per_minute", 30))
                    burst = int(rl.get("burst", 5))
                    self._buckets[name] = _TokenBucket(cpm, burst)
        except Exception:  # noqa: BLE001
            pass
        self._init_defaults()

    def _init_defaults(self) -> None:
        """Populate missing providers with default limits."""
        for provider, limits in self._DEFAULTS.items():
            if provider not in self._buckets:
                self._buckets[provider] = _TokenBucket(
                    limits["calls_per_minute"], limits["burst"]
                )

    def check(self, provider: str) -> None:
        """Assert that an API call to ``provider`` is within its rate limit.

        Raises:
            RateLimitExceeded: if the rate limit is reached.
        """
        bucket = self._buckets.get(provider)
        if bucket is None:
            # Unknown provider — allow (no config, no limit)
            return
        if not bucket.check():
            raise RateLimitExceeded(provider, bucket.calls_per_minute)

    # StateMiddleware interface (pass-through for state writes)

    def before_write(
        self,
        domain: str,
        current_content: str,
        proposed_content: str,
        ctx: Any | None = None,
    ) -> str | None:
        return proposed_content  # Rate limiter does not restrict state writes

    def after_write(self, domain: str, file_path: Path) -> None:
        pass

    def before_step(self, step_name: str, context: dict, data: Any) -> None:
        pass

    def after_step(self, step_name: str, context: dict, data: Any) -> None:
        pass

    def on_error(self, step_name: str, context: dict, error: Exception) -> None:
        pass
