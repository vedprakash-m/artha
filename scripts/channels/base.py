# pii-guard: ignore-file — protocol definitions; no personal data
"""
scripts/channels/base.py — ChannelAdapter Protocol and message dataclasses.

Mirrors scripts/connectors/base.py in structure and rationale.
Adapters are duck-typed modules — no inheritance required.
Any module that implements the four methods IS a ChannelAdapter.

Ref: specs/conversational-bridge.md §4
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ChannelMessage:
    """Immutable outbound message envelope.

    text:         Platform-agnostic message text (adapter renders to platform format).
    recipient_id: Platform-specific recipient identifier (chat_id, user_id, etc.).
    buttons:      Optional inline action buttons. Each: {"label": str, "command": str}.
    parse_mode:   Optional format hint ("HTML", "MarkdownV2", or "" for plain text).
    """

    text: str
    recipient_id: str
    buttons: list[dict[str, str]] = field(default_factory=list)
    parse_mode: str = ""


@dataclass(frozen=True)
class InboundMessage:
    """Parsed inbound command from a channel.

    sender_id:   Platform-specific sender identifier (used for whitelist check).
    sender_name: Human-readable name for audit logs.
    command:     Slash command, e.g. "/status", "/alerts". Lowercased.
    args:        Command arguments, e.g. ["immigration"] for "/domain immigration".
    raw_text:    Full original message text (for audit/logging only).
    timestamp:   ISO 8601 message timestamp (used for stale message detection).
    message_id:  Platform-specific unique ID for deduplication.
    """

    sender_id: str
    sender_name: str
    command: str
    args: list[str]
    raw_text: str
    timestamp: str
    message_id: str


@runtime_checkable
class ChannelAdapter(Protocol):
    """Structural typing protocol for Artha outbound messaging channels.

    Mirrors ConnectorHandler from scripts/connectors/base.py.
    Adapters are standalone modules loaded via importlib — no base class import needed.
    Duck typing: any object implementing these four methods satisfies the protocol.

    Layer 1 (Push) requires: send_message, send_document, health_check
    Layer 2 (Interactive) additionally requires: poll

    Example minimal adapter::

        # scripts/channels/my_platform.py

        def create_adapter(*, credential_key: str, **config) -> object:
            token = keyring.get_password("artha", credential_key)
            return MyPlatformAdapter(token=token, **config)

        def platform_name() -> str:
            return "My Platform"

        class MyPlatformAdapter:
            def send_message(self, message: ChannelMessage) -> bool: ...
            def send_document(self, *, recipient_id, file_path, caption="") -> bool: ...
            def health_check(self) -> bool: ...
            def poll(self, *, timeout=30) -> list: raise NotImplementedError
    """

    def send_message(self, message: ChannelMessage) -> bool:
        """Send a text message (with optional buttons) to a recipient.

        Returns True on success, False on failure.
        MUST NOT raise exceptions — return False instead.
        """
        ...  # pragma: no cover

    def send_document(self, *, recipient_id: str,
                      file_path: str, caption: str = "") -> bool:
        """Send a file to a recipient.

        Returns True on success, False on failure.
        MUST NOT raise exceptions — return False instead.
        Only sends files with sensitivity=standard. Callers are responsible
        for sensitivity checks before calling this method.
        """
        ...  # pragma: no cover

    def health_check(self) -> bool:
        """Verify bot token validity and API reachability.

        Returns True if healthy, False otherwise.
        Called by preflight.py P1 check and on listener startup.
        MUST NOT raise exceptions.
        """
        ...  # pragma: no cover

    def poll(self, *, timeout: int = 30) -> list[InboundMessage]:
        """Long-poll for inbound messages. Returns empty list on timeout/error.

        Only required for Layer 2 (interactive daemon).
        Push-only adapters should raise NotImplementedError.
        Must update internal offset/cursor so each call returns only new messages.
        """
        ...  # pragma: no cover
