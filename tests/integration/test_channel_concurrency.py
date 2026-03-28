"""tests/integration/test_channel_concurrency.py — WS-9-B concurrency gates.

Ref: specs/pay-debt-reloaded.md §11.3 Phase WS9-B step 5

Two integration-level concurrency tests that verify the threading.Lock
correctness of channel.security primitives under concurrent load:

  - test_deduplicator_concurrent_access
  - test_rate_limiter_thread_safety
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from channel.security import _MessageDeduplicator, _RateLimiter


# ---------------------------------------------------------------------------
# WS-9-B gate 1: _MessageDeduplicator thread-safety under concurrent access
# ---------------------------------------------------------------------------

class TestDeduplicatorConcurrentAccess:
    """Verify _MessageDeduplicator is safe under simultaneous thread + asyncio access."""

    def test_deduplicator_concurrent_access(self):
        """Two threads + asyncio loop hammer simultaneously; assert no corruption.

        Each unique message_id must be accepted exactly once across all workers.
        If threading.Lock is absent or broken, two workers could both see the
        same message_id as "new" (race on deque membership check + append),
        producing a count > 1.

        The ``await asyncio.sleep(0)`` in the async path flushes the event loop
        between iterations, detecting potential starvation if an asyncio.Lock
        were incorrectly substituted for threading.Lock.
        """
        import asyncio
        import threading
        from collections import Counter

        N_MSG = 200
        N_THREADS = 2
        ded = _MessageDeduplicator(max_size=5000)
        msg_ids = [f"c-{i:04d}" for i in range(N_MSG)]

        accepted: Counter[str] = Counter()
        counter_lock = threading.Lock()
        start_event = threading.Event()

        def thread_worker() -> None:
            start_event.wait()  # Synchronise with async_worker start
            for mid in msg_ids:
                if not ded.is_duplicate(mid):
                    with counter_lock:
                        accepted[mid] += 1

        async def async_worker() -> None:
            start_event.set()  # Unblock all threads simultaneously
            for mid in msg_ids:
                if not ded.is_duplicate(mid):
                    with counter_lock:
                        accepted[mid] += 1
                await asyncio.sleep(0)  # Yield event loop; exposes starvation bugs

        threads = [threading.Thread(target=thread_worker) for _ in range(N_THREADS)]
        for t in threads:
            t.start()
        asyncio.run(async_worker())
        for t in threads:
            t.join()

        for mid in msg_ids:
            count = accepted[mid]
            assert count == 1, (
                f"Message {mid!r} accepted {count} times (expected exactly 1) — "
                "threading.Lock race condition detected in _MessageDeduplicator"
            )


# ---------------------------------------------------------------------------
# WS-9-B gate 2: _RateLimiter thread-safety under concurrent access
# ---------------------------------------------------------------------------

class TestRateLimiterThreadSafety:
    """Verify _RateLimiter is safe under simultaneous thread access."""

    def test_rate_limiter_thread_safety(self):
        """Concurrent rate limit checks must never allow more calls than max_per_window.

        Ten threads all hit is_rate_limited for the same sender simultaneously.
        If threading.Lock is absent, the sliding-window check becomes a TOCTOU
        race: multiple threads could read len(ts_list) < max before any writes,
        then all append — effectively bypassing the rate limit.
        """
        import threading

        N_THREADS = 10
        MAX_ALLOWED = 5
        limiter = _RateLimiter(max_per_window=MAX_ALLOWED, window_sec=60, cooldown_sec=60)
        sender = "concurrent-sender"

        allowed: list[bool] = []
        result_lock = threading.Lock()
        barrier = threading.Barrier(N_THREADS)

        def checker() -> None:
            barrier.wait()  # All threads start simultaneously for max contention
            result = limiter.is_rate_limited(sender)
            with result_lock:
                allowed.append(not result)  # True = allowed (not rate-limited)

        threads = [threading.Thread(target=checker) for _ in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total_allowed = sum(allowed)
        assert total_allowed <= MAX_ALLOWED, (
            f"Rate limit bypassed under concurrency: {total_allowed} calls allowed, "
            f"max is {MAX_ALLOWED} — threading.Lock race condition detected in _RateLimiter"
        )
