# pii-guard: ignore-file — shared infrastructure; no personal data
"""
scripts/lib/retry.py — Configurable retry with exponential backoff.

Replaces ~50 lines of copy-pasted retry logic in each of the 6 fetch scripts.
Drop-in decorator that handles HTTP 429, 500-504, rate-limit, and quota errors.

Usage:
    from scripts.lib.retry import with_retry

    @with_retry(max_retries=3, context="gmail list")
    def fetch_page():
        return service.users().messages().list(...).execute()

    # Or direct call form (for lambdas):
    result = with_retry(lambda: api.call(), max_retries=3, context="calendar")

Ref: remediation.md §6.4, standardization.md §7.5.1
"""
from __future__ import annotations

import time
import sys
from functools import wraps
from typing import Any, Callable, Optional, Set, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])

# HTTP status codes that warrant a retry (rate-limit + server errors)
RETRYABLE_CODES: Set[int] = {429, 500, 502, 503, 504}

# Substring patterns in exception messages that indicate a retryable error
RETRYABLE_PHRASES = (
    "rate limit",
    "quota",
    "throttl",
    "temporarily unavailable",
    "service unavailable",
    "too many requests",
    "backend error",
)

_DEFAULT_MAX_RETRIES  = 3
_DEFAULT_BASE_DELAY   = 1.0    # seconds before first retry
_DEFAULT_BACKOFF_MULT = 2.0    # multiply delay on each retry
_DEFAULT_MAX_DELAY    = 30.0   # cap per-wait at 30 seconds


def _is_retryable(exc: Exception) -> bool:
    """Return True if *exc* is an HTTP / quota error that warrants a retry."""
    exc_str = str(exc).lower()
    if any(str(code) in exc_str for code in RETRYABLE_CODES):
        return True
    return any(phrase in exc_str for phrase in RETRYABLE_PHRASES)


def with_retry(
    fn: Optional[Callable] = None,
    *,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    base_delay: float = _DEFAULT_BASE_DELAY,
    backoff_mult: float = _DEFAULT_BACKOFF_MULT,
    max_delay: float = _DEFAULT_MAX_DELAY,
    context: str = "",
    retryable_codes: Optional[Set[int]] = None,
    label: str = "",
) -> Any:
    """Retry *fn* with exponential back-off on transient API errors.

    Can be used as a decorator or as a direct call:

        # Decorator
        @with_retry(max_retries=3, context="gmail list")
        def my_call(): ...

        # Direct call
        result = with_retry(lambda: api.call(), context="calendar fetch")

    Args:
        fn:             Zero-argument callable to retry. When used as a
                        decorator factory, omit this arg.
        max_retries:    Maximum number of *re-tries* after the first attempt.
        base_delay:     Initial wait in seconds between attempts.
        backoff_mult:   Multiplier applied to delay after each failure.
        max_delay:      Hard cap on per-attempt wait time (seconds).
        context:        Label shown in stderr log messages.
        retryable_codes: Override default RETRYABLE_CODES set.
        label:          Script prefix for log messages (e.g. "gmail_fetch").

    Returns:
        The return value of fn() on success.

    Raises:
        The original exception after all retries are exhausted, with retry
        context prepended to the message.
    """
    effective_codes = retryable_codes if retryable_codes is not None else RETRYABLE_CODES
    ctx = context or ""
    pfx = f"[{label}]" if label else ""

    def _execute(callable_fn: Callable) -> Any:
        delay = base_delay
        last_exc: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                return callable_fn()
            except Exception as exc:
                exc_str = str(exc).lower()
                is_retry = (
                    any(str(c) in exc_str for c in effective_codes)
                    or any(p in exc_str for p in RETRYABLE_PHRASES)
                )
                if not is_retry or attempt == max_retries:
                    ctx_str = f" [{ctx}]" if ctx else ""
                    raise type(exc)(
                        f"{pfx}{ctx_str} failed after {attempt + 1} attempt(s): {exc}"
                    ) from exc
                wait = min(delay, max_delay)
                print(
                    f"{pfx} ⚠ Rate-limited or server error"
                    f"{(' (' + ctx + ')') if ctx else ''}"
                    f" attempt {attempt + 1}/{max_retries + 1}."
                    f" Retrying in {wait:.0f}s ...",
                    file=sys.stderr,
                )
                time.sleep(wait)
                delay = min(delay * backoff_mult, max_delay)
                last_exc = exc
        raise last_exc  # unreachable — satisfies type checkers

    # Support both: direct call with fn, and decorator factory without fn
    if fn is not None:
        # Direct call: with_retry(lambda: ..., context="...")
        return _execute(fn)

    # Decorator factory: @with_retry(max_retries=3)
    def decorator(func: _F) -> _F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            return _execute(lambda: func(*args, **kwargs))
        return wrapper  # type: ignore[return-value]

    return decorator
