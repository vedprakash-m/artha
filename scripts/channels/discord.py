# pii-guard: ignore-file — Discord Bot API adapter; tokens loaded from keyring
"""
scripts/channels/discord.py — Discord channel adapter (Layer 1 push + Layer 2 Gateway).

Layer 1 (Push):
  send_message()   — posts via channels/{channel_id}/messages REST API
  send_document()  — uploads attachment file via multipart form data
  health_check()   — verifies bot token via GET /users/@me

Layer 2 (Interactive, requires Gateway connection):
  poll()           — receives inbound events via Discord Gateway WebSocket
                     Identifies as IDENTIFY payload, listens for MESSAGE_CREATE
                     Enforces sender_whitelist if configured

Authentication:
  Bot token: keyring key "artha-discord-bot-token"
             or env fallback ARTHA_DISCORD_BOT_TOKEN (CI/testing)

Required bot permissions (scopes):
  READ_MESSAGES, SEND_MESSAGES, ATTACH_FILES, READ_MESSAGE_HISTORY

Gateway intents:
  GUILD_MESSAGES (512), DIRECT_MESSAGES (4096), MESSAGE_CONTENT (32768)

Ref: specs/connect.md §8.1 (Discord Gateway), §8.2 (Layer 1 push)
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# Ensure Artha root is on sys.path
_ARTHA_DIR = Path(__file__).resolve().parent.parent.parent
if str(_ARTHA_DIR) not in sys.path:
    sys.path.insert(0, str(_ARTHA_DIR))

from channels.base import ChannelMessage, InboundMessage  # type: ignore[import]

log = logging.getLogger(__name__)

_API_BASE = "https://discord.com/api/v10"
_GATEWAY_VERSION = 10
_GATEWAY_API_URL = f"{_API_BASE}/gateway/bot"
_BOT_ME_URL = f"{_API_BASE}/users/@me"

# Discord Gateway opcode constants
_OP_DISPATCH = 0
_OP_HEARTBEAT = 1
_OP_IDENTIFY = 2
_OP_RECONNECT = 7
_OP_HELLO = 10
_OP_HEARTBEAT_ACK = 11

# Discord Gateway intents
_INTENT_GUILD_MESSAGES = 1 << 9       # 512
_INTENT_DIRECT_MESSAGES = 1 << 12     # 4096
_INTENT_MESSAGE_CONTENT = 1 << 15     # 32768
_DEFAULT_INTENTS = _INTENT_GUILD_MESSAGES | _INTENT_DIRECT_MESSAGES | _INTENT_MESSAGE_CONTENT

# Retry settings for outbound API calls
_RETRY_MAX = 3
_RETRY_BASE_DELAY = 1.0
_MAX_TEXT_LENGTH = 2000  # Discord message length limit


# ---------------------------------------------------------------------------
# Low-level REST helpers
# ---------------------------------------------------------------------------

def _discord_request(
    token: str,
    method: str,
    endpoint: str,
    payload: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Make an authenticated REST request to Discord API.

    Handles 429 rate limits with Retry-After header.
    Returns parsed JSON response dict.
    Raises urllib.error.HTTPError on non-2xx responses.
    """
    url = f"{_API_BASE}/{endpoint.lstrip('/')}"

    for attempt in range(_RETRY_MAX):
        if files:
            # Multipart form data for file upload
            boundary = f"----------boundary{int(time.time())}"
            body_parts: list[bytes] = []
            if payload:
                body_parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
                    f"Content-Type: application/json\r\n\r\n{json.dumps(payload)}\r\n".encode()
                )
            for field_name, (filename, filedata, content_type) in files.items():
                body_parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"'
                    f'; filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode()
                    + filedata
                    + b"\r\n"
                )
            body_parts.append(f"--{boundary}--\r\n".encode())
            body = b"".join(body_parts)
            content_type_header = f"multipart/form-data; boundary={boundary}"
        else:
            body = json.dumps(payload or {}).encode()
            content_type_header = "application/json"

        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": content_type_header,
                "User-Agent": "ArthaBot/1.0 Python",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = float(exc.headers.get("Retry-After", _RETRY_BASE_DELAY))
                log.warning("Discord rate limited; retrying in %.1fs (attempt %d)", retry_after, attempt + 1)
                time.sleep(retry_after)
                continue
            raise
    raise RuntimeError(f"Discord request failed after {_RETRY_MAX} attempts: {method} {endpoint}")


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------

def _load_token(credential_key: str | None = None) -> str:
    """Resolve Discord bot token: keyring → env.

    Raises RuntimeError if neither source provides a token.
    """
    cred_key = credential_key or "artha-discord-bot-token"

    # Try keyring first
    try:
        import keyring  # type: ignore[import]
        token = keyring.get_password(cred_key, "token")
        if token:
            return token
    except ImportError:
        pass

    # Fallback: environment variable (CI/testing)
    token = os.environ.get("ARTHA_DISCORD_BOT_TOKEN", "")
    if token:
        return token

    raise RuntimeError(
        f"Discord bot token not found. Run 'python scripts/setup_discord.py' to configure."
    )


# ---------------------------------------------------------------------------
# Inbound message parsing
# ---------------------------------------------------------------------------

def _parse_message_create(event_data: dict[str, Any]) -> InboundMessage | None:
    """Parse a MESSAGE_CREATE Discord Gateway event into an InboundMessage.

    Returns None if the message is not a command (doesn't start with /).
    Ignores bot messages (author.bot=True).
    """
    if event_data.get("author", {}).get("bot"):
        return None

    content: str = event_data.get("content", "").strip()
    if not content.startswith("/"):
        return None

    parts = content.split()
    command = parts[0].lower()
    args = parts[1:] if len(parts) > 1 else []

    author = event_data.get("author", {})
    sender_id = str(author.get("id", ""))
    sender_name = author.get("username", "unknown")
    discriminator = author.get("discriminator", "")
    if discriminator and discriminator != "0":
        sender_name = f"{sender_name}#{discriminator}"

    # Discord message timestamp is ISO 8601
    ts_raw = event_data.get("timestamp", "")
    if ts_raw:
        try:
            ts_parsed = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            timestamp = ts_parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            timestamp = ts_raw
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return InboundMessage(
        sender_id=sender_id,
        sender_name=sender_name,
        command=command,
        args=args,
        raw_text=content,
        timestamp=timestamp,
        message_id=str(event_data.get("id", "")),
    )


# ---------------------------------------------------------------------------
# DiscordAdapter
# ---------------------------------------------------------------------------

@dataclass
class DiscordAdapter:
    """Discord bot adapter — Layer 1 (push) + Layer 2 (Gateway interactive).

    Instantiate via create_adapter() factory function.
    """

    token: str
    channel_id: str = ""            # Default send-to channel (Snowflake ID)
    sender_whitelist: list[str] = field(default_factory=list)  # User IDs
    gateway_intents: int = _DEFAULT_INTENTS

    # Internal state for Gateway session
    _session_id: str = field(default="", init=False, repr=False)
    _last_sequence: int | None = field(default=None, init=False, repr=False)

    def send_message(self, message: ChannelMessage) -> bool:
        """Send a text message to a Discord channel.

        recipient_id should be a Discord channel Snowflake ID.
        Text longer than 2000 chars is chunked automatically.
        """
        channel_id = message.recipient_id or self.channel_id
        if not channel_id:
            log.error("discord send_message: no channel_id specified")
            return False

        text = message.text
        # Chunk if over limit
        chunks: list[str] = []
        while len(text) > _MAX_TEXT_LENGTH:
            split_at = text.rfind("\n", 0, _MAX_TEXT_LENGTH)
            if split_at <= 0:
                split_at = _MAX_TEXT_LENGTH
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip()
        if text:
            chunks.append(text)

        success = True
        for chunk in chunks:
            payload: dict[str, Any] = {"content": chunk}
            if message.buttons:
                # Discord uses components (button rows) for interactive elements
                # We send button labels as a text fallback for simplicity
                btn_text = "\n".join(f"  • {b['label']}: `{b['command']}`" for b in message.buttons)
                payload["content"] = f"{chunk}\n{btn_text}"
            try:
                _discord_request(self.token, "POST", f"channels/{channel_id}/messages", payload=payload)
            except Exception as exc:
                log.error("discord send_message failed: %s", exc)
                success = False
        return success

    def send_document(self, *, recipient_id: str, file_path: Path | str, caption: str = "") -> bool:
        """Upload a file to a Discord channel as an attachment."""
        channel_id = recipient_id or self.channel_id
        if not channel_id:
            log.error("discord send_document: no channel_id specified")
            return False

        fp = Path(file_path)
        if not fp.exists():
            log.error("discord send_document: file not found: %s", fp)
            return False

        mime_type = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
        file_data = fp.read_bytes()
        files = {"file": (fp.name, file_data, mime_type)}
        payload = {"content": caption} if caption else {}

        try:
            _discord_request(
                self.token,
                "POST",
                f"channels/{channel_id}/messages",
                payload=payload,
                files=files,
            )
            return True
        except Exception as exc:
            log.error("discord send_document failed: %s", exc)
            return False

    def health_check(self) -> bool:
        """Verify bot token by calling GET /users/@me."""
        try:
            result = _discord_request(self.token, "GET", "users/@me")
            bot_id = result.get("id")
            bot_username = result.get("username", "unknown")
            log.info("discord health_check OK: %s (%s)", bot_username, bot_id)
            return True
        except Exception as exc:
            log.error("discord health_check failed: %s", exc)
            return False

    def poll(
        self,
        *,
        channels: list[str] | None = None,
        timeout: float = 30.0,
    ) -> Iterator[InboundMessage]:
        """Connect to Discord Gateway and yield inbound commands.

        Uses websockets.sync.client for blocking WebSocket connection.
        Sends IDENTIFY payload with guild_messages + direct_messages + message_content intents.
        Each MESSAGE_CREATE event is parsed; only slash commands are yielded.
        sender_whitelist is enforced: events from non-whitelisted senders are dropped.

        Reconnects automatically on RECONNECT (opcode 7) or connection close.
        """
        try:
            import websockets.sync.client as ws_sync  # type: ignore[import]
        except ImportError:
            log.error("discord poll: 'websockets' package required. pip install websockets")
            return

        # Fetch Gateway URL with bot endpoint (includes recommended shards)
        try:
            gw_data = _discord_request(self.token, "GET", "gateway/bot")
            gateway_url = gw_data.get("url", "wss://gateway.discord.gg")
        except Exception as exc:
            log.error("discord poll: failed to fetch gateway URL: %s", exc)
            return

        gateway_url = f"{gateway_url}/?v={_GATEWAY_VERSION}&encoding=json"
        channel_filter: set[str] | None = set(channels) if channels else None

        reconnect = True
        resume_url: str | None = None
        resume_token: str | None = None

        while reconnect:
            connect_url = resume_url or gateway_url
            try:
                with ws_sync.connect(connect_url, open_timeout=10) as ws:
                    heartbeat_interval: float | None = None
                    last_beat_time: float = 0.0
                    beat_thread: threading.Thread | None = None
                    beat_stop = threading.Event()

                    def _heartbeat_loop(interval_ms: float, stop_event: threading.Event) -> None:
                        nonlocal ws
                        while not stop_event.wait(interval_ms / 1000.0 * 0.9):
                            try:
                                ws.send(json.dumps({"op": _OP_HEARTBEAT, "d": self._last_sequence}))
                            except Exception:
                                break

                    for raw_msg in ws:
                        msg = json.loads(raw_msg)
                        op = msg.get("op")
                        data = msg.get("d")
                        s = msg.get("s")
                        t = msg.get("t")

                        if s is not None:
                            self._last_sequence = s

                        if op == _OP_HELLO:
                            heartbeat_interval = data.get("heartbeat_interval", 41250)
                            # Start heartbeat thread
                            beat_stop.clear()
                            beat_thread = threading.Thread(
                                target=_heartbeat_loop,
                                args=(heartbeat_interval, beat_stop),
                                daemon=True,
                            )
                            beat_thread.start()

                            # Send IDENTIFY
                            identify_payload = {
                                "op": _OP_IDENTIFY,
                                "d": {
                                    "token": self.token,
                                    "intents": self.gateway_intents,
                                    "properties": {
                                        "os": "linux",
                                        "browser": "artha",
                                        "device": "artha",
                                    },
                                },
                            }
                            ws.send(json.dumps(identify_payload))

                        elif op == _OP_DISPATCH:
                            if t == "READY":
                                self._session_id = data.get("session_id", "")
                                resume_url = data.get("resume_gateway_url", gateway_url)
                                log.info("discord Gateway READY, session_id=%s", self._session_id)

                            elif t == "MESSAGE_CREATE":
                                # Filter by channel if configured
                                msg_channel = str(data.get("channel_id", ""))
                                if channel_filter and msg_channel not in channel_filter:
                                    continue

                                inbound = _parse_message_create(data)
                                if inbound is None:
                                    continue

                                # Sender whitelist enforcement
                                if self.sender_whitelist and inbound.sender_id not in self.sender_whitelist:
                                    log.info(
                                        "discord poll: dropped message from non-whitelisted sender %s",
                                        inbound.sender_id,
                                    )
                                    continue

                                yield inbound

                        elif op == _OP_RECONNECT:
                            log.info("discord Gateway RECONNECT requested")
                            beat_stop.set()
                            resume_token = self.token
                            break

                        elif op == _OP_HEARTBEAT:
                            # Server-requested heartbeat
                            ws.send(json.dumps({"op": _OP_HEARTBEAT, "d": self._last_sequence}))

                        elif op == _OP_HEARTBEAT_ACK:
                            last_beat_time = time.time()

                    if beat_stop and not beat_stop.is_set():
                        beat_stop.set()

            except Exception as exc:
                log.warning("discord Gateway disconnected: %s — reconnecting…", exc)
                time.sleep(2.0)


# ---------------------------------------------------------------------------
# Module-level factory (ChannelAdapter protocol)
# ---------------------------------------------------------------------------

def create_adapter(
    *,
    credential_key: str | None = None,
    channel_id: str = "",
    sender_whitelist: list[str] | None = None,
    gateway_intents: int = _DEFAULT_INTENTS,
    **_kwargs: Any,
) -> "DiscordAdapter":
    """Create and return a DiscordAdapter instance.

    Parameters:
        credential_key:    Keyring service name for bot token (default: artha-discord-bot-token)
        channel_id:        Default Discord channel Snowflake ID for outbound messages
        sender_whitelist:  User ID allowlist for Layer 2 inbound commands (empty = any)
        gateway_intents:   Bitfield of Discord Gateway intents (default: guild+dm+content)
    """
    token = _load_token(credential_key)
    return DiscordAdapter(
        token=token,
        channel_id=channel_id,
        sender_whitelist=sender_whitelist or [],
        gateway_intents=gateway_intents,
    )


def platform_name() -> str:
    """Return the human-readable platform name for this adapter."""
    return "Discord"
