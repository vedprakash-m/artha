# pii-guard: ignore-file — Slack Bot API adapter; tokens loaded from keyring
"""
scripts/channels/slack.py — Slack channel adapter (Layer 1 push + Layer 2 Socket Mode).

Layer 1 (Push):
  send_message()   — posts via chat.postMessage
  send_document()  — uploads via files.getUploadURLExternal / files.completeUploadExternal
  health_check()   — verifies bot token via auth.test

Layer 2 (Interactive, requires app-level token xapp-…):
  poll()           — receives inbound events via Slack Socket Mode (WebSocket)
                     Acknowledges each event with {"envelope_id": id}
                     Enforces sender_whitelist if configured

Authentication:
  Bot token    (xoxb-…): keyring key "artha-slack-bot-token" (or credential_key param)
  App token    (xapp-…): keyring key "artha-slack-app-token" (or app_credential_key param)
  Environment fallbacks: ARTHA_SLACK_BOT_TOKEN, ARTHA_SLACK_APP_TOKEN (CI/testing)

Required OAuth scopes (bot token):
  chat:write, files:write, channels:history, channels:read,
  groups:history, groups:read, im:history, mpim:history, users:read

Required OAuth scopes (app token):
  connections:write  (needed for Socket Mode apps.connections.open)

Ref: specs/connect.md §7.4 (Slack Socket Mode), §7.5 (Layer 1 push)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
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

_API_BASE = "https://slack.com/api"

# Slack Web API hard limit for chat.postMessage text length
_MAX_TEXT_LENGTH = 40_000

# Retry settings for outbound API calls
_RETRY_MAX = 3
_RETRY_BASE_DELAY = 1.0
_RETRY_MAX_DELAY = 30.0


# ---------------------------------------------------------------------------
# Internal HTTP helper
# ---------------------------------------------------------------------------

def _slack_api(
    token: str,
    method: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """POST to a Slack Web API method. Returns parsed response dict.

    Raises:
        RuntimeError: on HTTP error or api ok=false (after retries for 429).
    """
    url = f"{_API_BASE}/{method}"
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )

    delay = _RETRY_BASE_DELAY
    last_exc: Exception | None = None
    for attempt in range(_RETRY_MAX + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"):
                err = data.get("error", "unknown_error")
                # Non-retryable auth errors
                if err in ("invalid_auth", "not_authed", "account_inactive", "token_revoked"):
                    raise RuntimeError(f"Slack [{method}] auth error: {err}")
                raise RuntimeError(f"Slack [{method}] error: {err}")
            return data
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = int(exc.headers.get("Retry-After", max(delay, 5)))
                wait = min(retry_after, _RETRY_MAX_DELAY)
                log.warning("[slack] %s rate-limited (429) — sleeping %ds", method, wait)
                last_exc = exc
                if attempt < _RETRY_MAX:
                    time.sleep(wait)
                    delay = min(delay * 2, _RETRY_MAX_DELAY)
                    continue
            raise RuntimeError(f"Slack [{method}] HTTP {exc.code}: {exc.reason}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_exc = exc
            if attempt < _RETRY_MAX:
                wait = min(delay, _RETRY_MAX_DELAY)
                log.warning("[slack] %s attempt %d/%d: %s — retry in %.0fs",
                            method, attempt + 1, _RETRY_MAX, exc, wait)
                time.sleep(wait)
                delay = min(delay * 2, _RETRY_MAX_DELAY)
                continue
            raise RuntimeError(
                f"Slack [{method}] failed after {_RETRY_MAX + 1} attempts: {last_exc}"
            ) from last_exc

    raise RuntimeError(f"Slack [{method}] failed: {last_exc}")


def _load_token(credential_key: str, env_var: str) -> str | None:
    """Load token from keyring, then environment variable."""
    try:
        import keyring  # type: ignore[import]
        token = keyring.get_password("artha", credential_key)
        if token:
            return token
    except Exception:
        pass
    return os.environ.get(env_var) or None


# ---------------------------------------------------------------------------
# Slack channel adapter class
# ---------------------------------------------------------------------------

@dataclass
class SlackAdapter:
    """Slack Bot API adapter implementing the ChannelAdapter protocol.

    token:              Bot token (xoxb-…) for sending messages.
    app_token:          App-level token (xapp-…) for Socket Mode. Optional.
    recipient_id:       Default channel/user ID for send_message calls.
    sender_whitelist:   If non-empty, poll() only returns messages from these user IDs.
    retry_max:          Max retry attempts on transient errors.
    retry_base_delay:   Initial delay (seconds) before first retry.
    retry_max_delay:    Maximum per-retry delay cap.
    """

    token: str
    app_token: str = ""
    recipient_id: str = ""
    sender_whitelist: list[str] = field(default_factory=list)
    retry_max: int = _RETRY_MAX
    retry_base_delay: float = _RETRY_BASE_DELAY
    retry_max_delay: float = _RETRY_MAX_DELAY
    _ws_client: Any = field(default=None, init=False, repr=False)

    # ── Layer 1: Outbound ─────────────────────────────────────────────────

    def send_message(self, message: ChannelMessage) -> bool:
        """Post a message to a Slack channel or DM. Returns True on success.

        Uses chat.postMessage. Text is truncated to Slack's 40 000 char limit.
        Never raises — returns False on error.
        """
        recipient = message.recipient_id or self.recipient_id
        if not recipient:
            log.error("[slack] send_message: no recipient_id specified")
            return False

        text = message.text[:_MAX_TEXT_LENGTH]
        payload: dict[str, Any] = {
            "channel": recipient,
            "text": text,
        }
        # Only set mrkdwn if we're sending formatted text
        if message.parse_mode:
            payload["mrkdwn"] = True

        # Inline buttons as Block Kit actions
        if message.buttons:
            actions = [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": btn["label"]},
                    "action_id": btn["command"],
                    "value": btn["command"],
                }
                for btn in message.buttons
            ]
            payload["blocks"] = [
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                {"type": "actions", "elements": actions},
            ]
            # Keep text as fallback notification text
        try:
            self._api("chat.postMessage", payload)
            return True
        except Exception as exc:
            log.error("[slack] send_message to %s failed: %s", recipient, exc)
            return False

    def send_document(
        self,
        *,
        recipient_id: str,
        file_path: str,
        caption: str = "",
    ) -> bool:
        """Upload a file to a Slack channel using the 3-step v2 Upload API.

        Steps:
          1. files.getUploadURLExternal — obtain pre-signed upload URL + file_id
          2. PUT file bytes to the pre-signed URL
          3. files.completeUploadExternal — share the file in the channel

        Returns True on success, False on error (never raises).
        """
        fpath = Path(file_path)
        if not fpath.is_file():
            log.error("[slack] send_document: file not found: %s", file_path)
            return False

        file_bytes = fpath.read_bytes()
        file_size = len(file_bytes)
        filename = fpath.name

        try:
            # Step 1: Get upload URL
            resp1 = self._api(
                "files.getUploadURLExternal",
                {"filename": filename, "length": file_size},
            )
            upload_url = resp1.get("upload_url", "")
            file_id = resp1.get("file_id", "")
            if not upload_url or not file_id:
                log.error("[slack] send_document: missing upload_url or file_id")
                return False

            # Step 2: PUT bytes to pre-signed URL (no auth header needed)
            put_req = urllib.request.Request(
                upload_url,
                data=file_bytes,
                headers={"Content-Type": "application/octet-stream"},
                method="PUT",
            )
            with urllib.request.urlopen(put_req, timeout=120) as put_resp:
                if put_resp.status not in (200, 204):
                    log.error(
                        "[slack] send_document: upload PUT returned HTTP %d",
                        put_resp.status,
                    )
                    return False

            # Step 3: Complete upload — share in channel
            complete_payload: dict[str, Any] = {
                "files": [{"id": file_id, "title": caption or filename}],
                "channel_id": recipient_id or self.recipient_id,
            }
            if caption:
                complete_payload["initial_comment"] = caption

            self._api("files.completeUploadExternal", complete_payload)
            return True

        except Exception as exc:
            log.error("[slack] send_document to %s failed: %s", recipient_id, exc)
            return False

    def health_check(self) -> bool:
        """Verify bot token via auth.test. Returns True if healthy (never raises)."""
        try:
            resp = self._api("auth.test", timeout=10)
            log.info(
                "[slack] health_check OK — team: %s, bot: %s",
                resp.get("team", "?"),
                resp.get("user", "?"),
            )
            return True
        except Exception as exc:
            log.warning("[slack] health_check failed: %s", exc)
            return False

    # ── Layer 2: Inbound polling (Socket Mode) ────────────────────────────

    def poll(self, *, timeout: int = 30) -> list[InboundMessage]:
        """Receive inbound commands via Slack Socket Mode.

        Requires an app-level token (xapp-…). Returns parsed InboundMessage list.
        Acknowledges each envelope to prevent re-delivery.
        Filters inbound messages against sender_whitelist if configured.
        Returns empty list if app_token not set or on error (never raises).
        """
        if not self.app_token:
            log.debug("[slack] poll: no app_token — Socket Mode disabled")
            return []

        # Lazy initialise WebSocket client
        if self._ws_client is None:
            try:
                from lib.websocket_client import WebSocketClient  # type: ignore[import]
            except ImportError:
                log.error("[slack] poll: WebSocketClient not available")
                return []

            def _url_factory() -> str:
                resp = _slack_api(self.app_token, "apps.connections.open")
                return resp["url"]

            self._ws_client = WebSocketClient(
                url_factory=_url_factory,
                ping_interval=20.0,
                ping_timeout=10.0,
                reconnect=True,
                max_reconnects=0,
            )

        try:
            events = self._ws_client.poll(timeout=float(timeout))
        except Exception as exc:
            log.warning("[slack] poll WebSocket error: %s", exc)
            return []

        messages: list[InboundMessage] = []
        for envelope in events:
            env_id = envelope.get("envelope_id", "")

            # Acknowledge the envelope to prevent re-delivery
            if env_id:
                self._ws_client.send({"envelope_id": env_id})

            # Only process "events_api" type envelopes containing messages
            if envelope.get("type") != "events_api":
                continue

            payload = envelope.get("payload", {})
            event = payload.get("event", {})
            if event.get("type") not in ("message", "app_mention"):
                continue

            # Skip bot messages to prevent loops
            if event.get("bot_id") or event.get("subtype"):
                continue

            sender_id = event.get("user", "")
            text = event.get("text", "").strip()
            ts = event.get("ts", "")
            channel = event.get("channel", "")

            if not sender_id or not text:
                continue

            # Enforce sender whitelist
            if self.sender_whitelist and sender_id not in self.sender_whitelist:
                log.debug("[slack] poll: ignoring message from non-whitelisted user %s", sender_id)
                continue

            # Parse ISO timestamp from Slack's Unix ts string
            try:
                ts_iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            except (ValueError, TypeError):
                ts_iso = datetime.now(timezone.utc).isoformat()

            # Parse command: first word starting with "/" or the entire text if no slash
            parts = text.strip().split()
            if not parts:
                continue
            raw_cmd = parts[0].lower()
            command = raw_cmd if raw_cmd.startswith("/") else f"/{raw_cmd}"
            args = parts[1:] if len(parts) > 1 else []

            messages.append(InboundMessage(
                sender_id=sender_id,
                sender_name=sender_id,  # name lookup deferred to channel_listener
                command=command,
                args=args,
                raw_text=text,
                timestamp=ts_iso,
                message_id=f"slack_{channel}_{ts.replace('.', '')}",
            ))

        return messages

    # ── Internal HTTP wrapper ─────────────────────────────────────────────

    def _api(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Dispatch a Slack API call using this adapter's bot token."""
        return _slack_api(self.token, method, payload, timeout=timeout)


# ---------------------------------------------------------------------------
# Module-level factory (ChannelAdapter protocol)
# ---------------------------------------------------------------------------

def create_adapter(
    *,
    credential_key: str = "artha-slack-bot-token",
    app_credential_key: str = "artha-slack-app-token",
    recipient_id: str = "",
    sender_whitelist: list[str] | None = None,
    retry_max: int = _RETRY_MAX,
    retry_base_delay: float = _RETRY_BASE_DELAY,
    retry_max_delay: float = _RETRY_MAX_DELAY,
    **_kwargs: Any,
) -> SlackAdapter:
    """Factory function. Loads tokens from keyring, returns SlackAdapter.

    Token lookup order (bot token):
      1. OS keyring (service="artha", key=credential_key)
      2. ARTHA_SLACK_BOT_TOKEN environment variable

    Token lookup order (app token — optional, enables Socket Mode):
      1. OS keyring (service="artha", key=app_credential_key)
      2. ARTHA_SLACK_APP_TOKEN environment variable

    Raises:
        RuntimeError: if bot token is not found.
    """
    token = _load_token(credential_key, "ARTHA_SLACK_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            f"Slack bot token not found (keyring key: '{credential_key}', "
            "env: ARTHA_SLACK_BOT_TOKEN). Run scripts/setup_slack.py to configure."
        )

    app_token = _load_token(app_credential_key, "ARTHA_SLACK_APP_TOKEN") or ""

    return SlackAdapter(
        token=token,
        app_token=app_token,
        recipient_id=recipient_id,
        sender_whitelist=list(sender_whitelist or []),
        retry_max=retry_max,
        retry_base_delay=retry_base_delay,
        retry_max_delay=retry_max_delay,
    )


def platform_name() -> str:
    """Return the canonical platform name for this adapter."""
    return "Slack"
