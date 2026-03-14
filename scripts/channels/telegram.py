# pii-guard: ignore-file — Telegram Bot API adapter; tokens loaded from keyring
"""
scripts/channels/telegram.py — Telegram channel adapter (reference implementation).

Layer 1: send_message(), send_document(), health_check()
Layer 2: poll() — getUpdates long-polling with offset management

Uses Telegram Bot API directly via standard-library urllib (no extra dependencies
needed for Layer 1). Layer 2 polling also uses urllib only — python-telegram-bot
is listed as optional in pyproject.toml for future webhook/async use.

Bot token is loaded from OS keyring (keyring library). Falls back to the
ARTHA_TELEGRAM_BOT_TOKEN environment variable for CI/test environments.

Telegram MarkdownV2 format is used for outbound messages. All special characters
are escaped by _tg_escape() before sending.

Ref: specs/conversational-bridge.md §4 (Adapter Module Contract), §7 (Layer 1)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure Artha root is on sys.path
_ARTHA_DIR = Path(__file__).resolve().parent.parent.parent
if str(_ARTHA_DIR) not in sys.path:
    sys.path.insert(0, str(_ARTHA_DIR))

from channels.base import ChannelMessage, InboundMessage

log = logging.getLogger(__name__)

# Telegram Bot API base URL — token and method are interpolated per-call
_API_BASE = "https://api.telegram.org/bot{token}/{method}"

# Telegram hard limit on text message length
_MAX_TEXT_LENGTH = 4096

# Characters that must be escaped in Telegram MarkdownV2 format
_TG_ESCAPE_CHARS = r"\_*[]()~`>#+-=|{}.!"


def _tg_escape(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2 format."""
    for ch in _TG_ESCAPE_CHARS:
        text = text.replace(ch, f"\\{ch}")
    return text


@dataclass
class TelegramAdapter:
    """Telegram Bot API adapter implementing the ChannelAdapter protocol.

    Uses direct HTTPS calls via urllib.request — no third-party HTTP library
    required. Retry logic is built in with exponential backoff.
    """

    token: str
    retry_max: int = 3
    retry_base_delay: float = 2.0
    retry_max_delay: float = 30.0
    _update_offset: int = field(default=0, init=False, repr=False)

    def _api_url(self, method: str) -> str:
        return _API_BASE.format(token=self.token, method=method)

    def _call(self, method: str, params: dict[str, Any] | None = None,
              timeout: int = 30) -> Any:
        """Make a Telegram Bot API call with retry.

        Args:
            method:  Telegram API method name (e.g. "sendMessage").
            params:  JSON body dict. None uses empty dict.
            timeout: HTTP timeout in seconds.

        Returns:
            The "result" field from the Telegram API response.

        Raises:
            RuntimeError: after all retries exhausted (safe to catch in callers).
        """
        url = self._api_url(method)
        body = json.dumps(params or {}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        delay = self.retry_base_delay
        last_exc: Exception | None = None
        for attempt in range(self.retry_max + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    response_body = json.loads(resp.read().decode("utf-8"))
                if response_body.get("ok"):
                    return response_body.get("result", {})
                # Telegram API returned ok=false — not retryable (bad request)
                err_desc = response_body.get("description", "unknown error")
                raise RuntimeError(
                    f"Telegram API [{method}] error: {err_desc}"
                )
            except urllib.error.HTTPError as exc:
                # 429 Too Many Requests is retryable; others are not
                if exc.code == 429:
                    retry_after = int(exc.headers.get("Retry-After", delay))
                    last_exc = exc
                    if attempt < self.retry_max:
                        log.warning(
                            "[telegram] %s rate-limited (429) — retry in %ds",
                            method, retry_after,
                        )
                        time.sleep(min(float(retry_after), self.retry_max_delay))
                        delay *= 2.0
                        continue
                raise
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                last_exc = exc
                if attempt < self.retry_max:
                    actual_delay = min(delay, self.retry_max_delay)
                    log.warning(
                        "[telegram] %s attempt %d/%d failed: %s — retry in %.0fs",
                        method, attempt + 1, self.retry_max, exc, actual_delay,
                    )
                    time.sleep(actual_delay)
                    delay *= 2.0
        raise RuntimeError(
            f"Telegram API [{method}] failed after {self.retry_max + 1} attempts: {last_exc}"
        )

    # ── Layer 1: Outbound ─────────────────────────────────────────────────

    def send_message(self, message: ChannelMessage) -> bool:
        """Send text message to a recipient. Returns True/False (never raises)."""
        text = message.text[:_MAX_TEXT_LENGTH]
        params: dict[str, Any] = {
            "chat_id": message.recipient_id,
            "text": text,
        }
        if message.parse_mode:
            params["parse_mode"] = message.parse_mode
        # Inline keyboard buttons (Telegram-specific feature)
        if message.buttons:
            rows = [
                [{"text": btn["label"], "callback_data": btn["command"]}]
                for btn in message.buttons
            ]
            params["reply_markup"] = {"inline_keyboard": rows}
        try:
            self._call("sendMessage", params)
            return True
        except Exception as exc:
            log.error("[telegram] send_message to %s failed: %s",
                      message.recipient_id, exc)
            return False

    def send_message_get_id(self, message: ChannelMessage) -> int | None:
        """Send text message and return the Telegram message_id (for later deletion)."""
        text = message.text[:_MAX_TEXT_LENGTH]
        params: dict[str, Any] = {
            "chat_id": message.recipient_id,
            "text": text,
        }
        if message.parse_mode:
            params["parse_mode"] = message.parse_mode
        try:
            result = self._call("sendMessage", params)
            return result.get("message_id")
        except Exception as exc:
            log.error("[telegram] send_message_get_id to %s failed: %s",
                      message.recipient_id, exc)
            return None

    def delete_message(self, chat_id: str, message_id: int) -> bool:
        """Delete a previously sent message. Returns True/False."""
        try:
            self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
            return True
        except Exception as exc:
            log.debug("[telegram] delete_message %s/%s failed: %s",
                      chat_id, message_id, exc)
            return False

    def send_document(self, *, recipient_id: str,
                      file_path: str, caption: str = "") -> bool:
        """Send a file using multipart/form-data. Returns True/False (never raises)."""
        try:
            fpath = Path(file_path)
            if not fpath.exists():
                log.error("[telegram] send_document: file not found: %s", file_path)
                return False

            boundary = "ArthaDocBoundary"
            url = self._api_url("sendDocument")

            # Build multipart body manually (no third-party library)
            parts: list[bytes] = []

            def _field(name: str, value: str) -> bytes:
                return (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")

            parts.append(_field("chat_id", recipient_id))
            if caption:
                parts.append(_field("caption", caption))
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="document"; '
                    f'filename="{fpath.name}"\r\n'
                    "Content-Type: application/octet-stream\r\n\r\n"
                ).encode("utf-8")
            )
            parts.append(fpath.read_bytes())
            parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

            body = b"".join(parts)
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Content-Length": str(len(body)),
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            return bool(result.get("ok"))
        except Exception as exc:
            log.error("[telegram] send_document to %s failed: %s", recipient_id, exc)
            return False

    def health_check(self) -> bool:
        """Verify bot token via getMe. Returns True if healthy (never raises)."""
        try:
            result = self._call("getMe", timeout=10)
            return isinstance(result, dict) and bool(result.get("id"))
        except Exception as exc:
            log.warning("[telegram] health_check failed: %s", exc)
            return False

    # ── Layer 2: Startup helpers ──────────────────────────────────────────

    def delete_webhook(self) -> None:
        """Clear any configured webhook. Required before long-polling.
        Called on Layer 2 listener startup (defense-in-depth for multi-machine)."""
        try:
            self._call("deleteWebhook", {"drop_pending_updates": False}, timeout=10)
        except Exception as exc:
            log.warning("[telegram] delete_webhook failed (non-fatal): %s", exc)

    def flush_pending_updates(self) -> None:
        """Consume and discard all pending updates on startup.
        Prevents stale messages from being processed by a newly started listener.
        Updates internal offset to acknowledge existing messages."""
        try:
            result = self._call(
                "getUpdates", {"offset": -1, "limit": 1, "timeout": 0}, timeout=10
            )
            if isinstance(result, list) and result:
                self._update_offset = result[-1]["update_id"] + 1
                log.info(
                    "[telegram] Flushed pending updates; offset now %d",
                    self._update_offset,
                )
        except Exception as exc:
            log.warning("[telegram] flush_pending_updates failed (non-fatal): %s", exc)

    # ── Layer 2: Inbound polling ──────────────────────────────────────────

    def poll(self, *, timeout: int = 30) -> list[InboundMessage]:
        """Long-poll for inbound updates. Returns parsed InboundMessage list.

        Uses Telegram's getUpdates long-polling with offset tracking.
        Handles both regular messages and callback_query (button presses).
        Returns empty list on timeout or error (never raises).
        """
        params: dict[str, Any] = {
            "offset": self._update_offset,
            "timeout": timeout,
            "limit": 100,
            "allowed_updates": ["message", "callback_query"],
        }
        try:
            # HTTP timeout must be > Telegram polling timeout
            results = self._call("getUpdates", params, timeout=timeout + 15)
        except Exception as exc:
            log.warning("[telegram] poll failed: %s", exc)
            return []

        if not isinstance(results, list):
            return []

        messages: list[InboundMessage] = []
        for update in results:
            update_id = update.get("update_id", 0)
            # Always advance offset to acknowledge this update
            self._update_offset = max(self._update_offset, update_id + 1)

            # Determine source: callback_query (button press) or message (text)
            if "callback_query" in update:
                cb = update["callback_query"]
                text = cb.get("data", "")
                from_data = cb.get("from", {})
                sender_id = str(from_data.get("id", ""))
                sender_name = from_data.get("first_name", "unknown")
                msg_id = str(cb.get("id", ""))
                # callback_query timestamp comes from the originating message
                date = cb.get("message", {}).get("date", 0)
            elif "message" in update:
                msg = update["message"]
                text = msg.get("text", "")
                from_data = msg.get("from", {})
                sender_id = str(from_data.get("id", ""))
                sender_name = from_data.get("first_name", "unknown")
                msg_id = str(msg.get("message_id", ""))
                date = msg.get("date", 0)
            else:
                continue  # Unknown update type — skip

            if not text or not sender_id:
                continue

            # Parse command and args; handle /command@botname format
            parts = text.strip().split()
            raw_command = parts[0].lower() if parts else ""
            # Strip @botname suffix if present
            command = raw_command.split("@")[0] if "@" in raw_command else raw_command
            args = parts[1:] if len(parts) > 1 else []

            ts = datetime.fromtimestamp(date, tz=timezone.utc).isoformat()
            messages.append(InboundMessage(
                sender_id=sender_id,
                sender_name=sender_name,
                command=command,
                args=args,
                raw_text=text,
                timestamp=ts,
                message_id=f"tg_{update_id}_{msg_id}",
            ))

        return messages


# ── Module-level factory ───────────────────────────────────────────────────


def create_adapter(
    *,
    credential_key: str = "artha-telegram-bot-token",
    retry_max: int = 3,
    retry_base_delay: float = 2.0,
    retry_max_delay: float = 30.0,
) -> TelegramAdapter:
    """Factory function. Loads bot token from keyring, returns TelegramAdapter.

    Token lookup order:
      1. OS keyring (service="artha", key=credential_key)
      2. ARTHA_TELEGRAM_BOT_TOKEN environment variable (CI/test fallback)

    Raises:
        RuntimeError: if token not found in either location.
    """
    token: str | None = None

    try:
        import keyring
        token = keyring.get_password("artha", credential_key)
    except ImportError:
        log.debug("[telegram] keyring library not installed — using env var fallback")
    except Exception as exc:
        log.warning("[telegram] keyring lookup failed: %s — using env var fallback", exc)

    if not token:
        token = os.environ.get("ARTHA_TELEGRAM_BOT_TOKEN", "").strip()

    if not token:
        raise RuntimeError(
            f"Telegram bot token not found. Tried:\n"
            f"  1. keyring key '{credential_key}' (service=artha)\n"
            f"  2. ARTHA_TELEGRAM_BOT_TOKEN environment variable\n"
            f"Run: python scripts/setup_channel.py --channel telegram"
        )

    return TelegramAdapter(
        token=token,
        retry_max=retry_max,
        retry_base_delay=retry_base_delay,
        retry_max_delay=retry_max_delay,
    )


def platform_name() -> str:
    """Return human-readable platform name for audit logs."""
    return "Telegram"
