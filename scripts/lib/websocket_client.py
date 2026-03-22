# pii-guard: ignore-file — shared infrastructure; no personal data
"""
scripts/lib/websocket_client.py — Shared synchronous WebSocket client.

Used by:
  - scripts/channels/slack.py  (Slack Socket Mode — app-level token wss:// URL)
  - scripts/channels/discord.py (Discord Gateway — gateway wss:// URL) [Phase 2]

Features:
  - URL factory pattern: caller provides a callable that returns a fresh wss:// URL
    for each connection (required for Slack Socket Mode's one-time URLs).
  - Auto-reconnect with exponential backoff (1→2→4→...→30s, optional cap).
  - Synchronous API (websockets.sync.client) — no asyncio required.
  - Thread-safe close() via threading.Event.
  - Context manager support (__enter__ / __exit__).

Requires: websockets>=12.0 (pip install 'artha[channels]')

Ref: specs/connect.md §5.2 (shared_lib), §7.4 (Slack Socket Mode)
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)


class WebSocketClient:
    """Synchronous WebSocket client with auto-reconnect.

    Args:
        url_factory:      Callable[[], str] — called before each connection attempt
                          to obtain a fresh wss:// URL. For Slack Socket Mode this
                          calls apps.connections.open and returns the one-time URL.
        extra_headers:    Additional HTTP headers sent on the handshake (dict).
        ping_interval:    Seconds between keepalive pings (default 20).
        ping_timeout:     Seconds to wait for a pong reply (default 10).
        reconnect:        Whether to reconnect on disconnect (default True).
        max_reconnects:   Maximum reconnection attempts (0 = unlimited).
    """

    def __init__(
        self,
        *,
        url_factory: Callable[[], str],
        extra_headers: dict[str, str] | None = None,
        ping_interval: float = 20.0,
        ping_timeout: float = 10.0,
        reconnect: bool = True,
        max_reconnects: int = 0,
    ) -> None:
        self._url_factory = url_factory
        self._extra_headers = extra_headers or {}
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._reconnect = reconnect
        self._max_reconnects = max_reconnects

        self._ws: Any = None          # websockets.sync.client.ClientConnection
        self._closed = threading.Event()
        self._reconnect_count = 0

    # ── Connection lifecycle ──────────────────────────────────────────────

    def _connect(self) -> None:
        """Open a fresh WebSocket connection using a newly issued URL."""
        try:
            from websockets.sync.client import connect as ws_connect  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "websockets>=12.0 is required for WebSocketClient. "
                "Install with: pip install 'artha[channels]'"
            ) from exc

        url = self._url_factory()
        log.debug("[websocket] connecting to %s…", url[:60])
        self._ws = ws_connect(
            url,
            additional_headers=self._extra_headers,
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
            close_timeout=5,
        )
        self._reconnect_count = 0
        log.info("[websocket] connected")

    def _try_reconnect(self) -> bool:
        """Exponential-backoff reconnect. Returns True if successful."""
        if not self._reconnect or self._closed.is_set():
            return False
        if self._max_reconnects and self._reconnect_count >= self._max_reconnects:
            log.error(
                "[websocket] max reconnects (%d) reached — giving up",
                self._max_reconnects,
            )
            return False

        self._reconnect_count += 1
        delay = min(2 ** (self._reconnect_count - 1), 30)  # 1, 2, 4, 8, 16, 30, 30, …
        log.warning(
            "[websocket] disconnected — reconnect attempt %d in %ds",
            self._reconnect_count,
            delay,
        )
        time.sleep(delay)
        try:
            self._connect()
            return True
        except Exception as exc:
            log.error("[websocket] reconnect attempt %d failed: %s", self._reconnect_count, exc)
            return False

    def connect(self) -> None:
        """Explicitly connect (also called implicitly by poll on first use)."""
        if self._ws is None:
            self._connect()

    def close(self) -> None:
        """Close the WebSocket connection and stop reconnection attempts."""
        self._closed.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        log.debug("[websocket] closed")

    # ── IO ────────────────────────────────────────────────────────────────

    def poll(self, *, timeout: float = 30.0) -> list[dict[str, Any]]:
        """Receive all available messages within *timeout* seconds.

        Connects lazily on first call. Attempts reconnect on disconnect.
        Returns empty list on timeout, error, or when closed.
        Never raises — safe to call in a polling loop.

        Returns:
            List of parsed JSON dicts from the WebSocket server.
        """
        if self._closed.is_set():
            return []

        # Lazy connect
        if self._ws is None:
            try:
                self._connect()
            except Exception as exc:
                log.error("[websocket] initial connect failed: %s", exc)
                return []

        messages: list[dict[str, Any]] = []
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline and not self._closed.is_set():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                raw = self._ws.recv(timeout=min(remaining, 1.0))
            except TimeoutError:
                # websockets raises TimeoutError on recv timeout
                continue
            except Exception as exc:
                # Connection lost — attempt reconnect once
                log.warning("[websocket] recv error: %s", exc)
                if not self._try_reconnect():
                    break
                continue

            # Parse JSON payload
            try:
                data = json.loads(raw) if isinstance(raw, (str, bytes)) else {}
            except (json.JSONDecodeError, ValueError) as exc:
                log.debug("[websocket] non-JSON frame ignored: %s", exc)
                continue

            if isinstance(data, dict):
                messages.append(data)

        return messages

    def send(self, data: dict[str, Any]) -> bool:
        """Send a JSON message. Returns True on success, False on error.

        Never raises — safe to call after a poll() loop.
        """
        if self._ws is None or self._closed.is_set():
            return False
        try:
            self._ws.send(json.dumps(data))
            return True
        except Exception as exc:
            log.warning("[websocket] send failed: %s", exc)
            return False

    # ── Context manager ───────────────────────────────────────────────────

    def __enter__(self) -> "WebSocketClient":
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
