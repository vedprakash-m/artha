# pii-guard: ignore-file — shared infrastructure; no personal data
"""Small retry helper for idempotent provider reads."""
from __future__ import annotations

import random
import sys
import time
import math
from email.utils import parsedate_to_datetime
from functools import wraps
from numbers import Real
from typing import Any, Callable, Optional, Set, TypeVar
from urllib.error import URLError

_F = TypeVar("_F", bound=Callable[..., Any])

RETRYABLE_CODES: Set[int] = {429, 500, 502, 503, 504}

_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BASE_DELAY = 1.0
_DEFAULT_BACKOFF_MULT = 2.0
_DEFAULT_MAX_DELAY = 30.0
_DEFAULT_MAX_ELAPSED = 120.0


def _http_status(exc: BaseException) -> int | None:
    current: BaseException | None = exc
    for _ in range(8):
        if current is None:
            return None
        for owner in (current, getattr(current, "response", None), getattr(current, "resp", None)):
            if owner is None:
                continue
            for attribute in ("status_code", "status", "code"):
                value = getattr(owner, attribute, None)
                if type(value) is int:
                    return value
        current = current.__cause__ or current.__context__
    return None


def _is_retryable(
    exc: Exception,
    codes: set[int],
    retryable_exceptions: tuple[type[Exception], ...] = (),
) -> bool:
    status = _http_status(exc)
    if status is not None:
        return status in codes
    if isinstance(exc, retryable_exceptions):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError, URLError)):
        return True
    request_name = type(exc).__name__
    request_module = type(exc).__module__
    return request_module.startswith("requests.") and request_name in {
        "ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout", "ProxyError",
        "ChunkedEncodingError",
    }


def _response_headers(exc: BaseException) -> Any:
    current: BaseException | None = exc
    for _ in range(8):
        if current is None:
            return None
        for owner in (current, getattr(current, "response", None), getattr(current, "resp", None)):
            headers = getattr(owner, "headers", None)
            if headers is not None:
                return headers
        current = current.__cause__ or current.__context__
    return None


def _retry_after_seconds(exc: BaseException, now: float) -> float | None:
    headers = _response_headers(exc)
    if headers is None:
        return None
    try:
        raw = headers.get("Retry-After")
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        pass
    try:
        retry_at = parsedate_to_datetime(str(raw))
        return max(0.0, retry_at.timestamp() - time.time()) if now >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _add_note(exc: BaseException, context: str, attempt: int) -> None:
    add_note = getattr(exc, "add_note", None)
    if add_note is None or not context:
        return
    try:
        add_note(f"retry context={context[:120]} attempts={attempt}")
    except (AttributeError, TypeError):
        pass


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
    deadline: float | None = None,
    max_elapsed: float | None = None,
    idempotent: bool = True,
    retryable_exceptions: tuple[type[Exception], ...] = (),
) -> Any:
    """Call an idempotent operation with bounded structured-error retries.

    ``deadline`` is an absolute :func:`time.monotonic` value. ``max_elapsed`` is
    a convenience relative limit. The original exception object is always
    reraised; retry metadata is attached with ``Exception.add_note``.
    """
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
        raise ValueError("max_retries must be a nonnegative integer")
    if (
        not all(isinstance(value, Real) and not isinstance(value, bool) for value
                in (base_delay, backoff_mult, max_delay))
        or not all(math.isfinite(float(value)) for value in (base_delay, backoff_mult, max_delay))
        or base_delay < 0
        or backoff_mult < 1
        or max_delay < 0
    ):
        raise ValueError("retry delay values must be bounded and nonnegative")
    if deadline is not None and (
        isinstance(deadline, bool)
        or not isinstance(deadline, Real)
        or not math.isfinite(float(deadline))
    ):
        raise ValueError("deadline must be a monotonic timestamp")
    if max_elapsed is not None and (
        isinstance(max_elapsed, bool)
        or not isinstance(max_elapsed, Real)
        or not math.isfinite(float(max_elapsed))
        or max_elapsed < 0
    ):
        raise ValueError("max_elapsed must be nonnegative")

    effective_codes = set(retryable_codes if retryable_codes is not None else RETRYABLE_CODES)
    if any(isinstance(code, bool) or not isinstance(code, int) or not 100 <= code <= 599
           for code in effective_codes):
        raise ValueError("retryable_codes must contain HTTP status integers")
    pfx = f"[{label}]" if label else ""

    def _execute(callable_fn: Callable) -> Any:
        started = time.monotonic()
        expires = float(deadline) if deadline is not None else float("inf")
        if max_elapsed is not None:
            expires = min(expires, started + float(max_elapsed))
        elif deadline is None:
            expires = started + _DEFAULT_MAX_ELAPSED
        delay = min(float(base_delay), float(max_delay))
        for attempt in range(max_retries + 1):
            try:
                return callable_fn()
            except Exception as exc:
                _add_note(exc, context, attempt + 1)
                if (
                    not idempotent
                    or not _is_retryable(exc, effective_codes, retryable_exceptions)
                    or attempt == max_retries
                ):
                    raise
                now = time.monotonic()
                remaining = expires - now
                if remaining <= 0:
                    raise
                retry_after = _retry_after_seconds(exc, now)
                wait = max(delay, retry_after or 0.0)
                wait = min(wait, float(max_delay), remaining)
                jitter = random.uniform(0.0, min(delay * 0.25, max(0.0, remaining - wait)))
                wait = min(wait + jitter, float(max_delay), remaining)
                print(
                    f"{pfx} provider request retry"
                    f"{(' (' + context + ')') if context else ''}"
                    f" attempt {attempt + 1}/{max_retries + 1}. Retrying in {wait:.1f}s.",
                    file=sys.stderr,
                )
                if wait > 0:
                    time.sleep(wait)
                if time.monotonic() >= expires:
                    raise
                delay = min(delay * float(backoff_mult), float(max_delay))
        raise AssertionError("unreachable")

    if fn is not None:
        return _execute(fn)

    def decorator(func: _F) -> _F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return _execute(lambda: func(*args, **kwargs))

        return wrapper  # type: ignore[return-value]

    return decorator
