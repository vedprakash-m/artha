#!/usr/bin/env python3
# pii-guard: ignore-file — handler; PII guard applied by ActionExecutor before this runs
"""
scripts/actions/slack_send.py — Slack message send action handler.

Sends a message to a Slack channel or user via chat.postMessage.

Parameters (in proposal.parameters):
    channel:  Required. Slack channel ID (C…) or user ID (U…) or name (#general).
    text:     Required. Message text (mrkdwn supported).
    thread_ts: Optional. Thread parent timestamp for threaded replies.
    blocks:   Optional. JSON string of Slack Block Kit blocks (advanced formatting).

SAFETY:
  - autonomy_floor: true in actions.yaml — ALWAYS requires human approval.
  - dry_run: returns formatted preview without making any API call.
  - No undo window (Slack messages can be deleted but there is no guaranteed
    undo path within a fixed window — callers should use reversible=False).

API: Slack Web API chat.postMessage
Scope: chat:write (already configured in setup_slack.py)

Ref: specs/connect.md §7.5 (slack_send), specs/act.md §8 (ActionHandler protocol)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from actions.base import ActionProposal, ActionResult

log = logging.getLogger(__name__)

_API_BASE = "https://slack.com/api"
_MAX_TEXT_LENGTH = 40_000
_DEFAULT_CREDENTIAL_KEY = "artha-slack-bot-token"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_token() -> str | None:
    """Load Slack bot token from keyring or environment variable."""
    try:
        import keyring  # type: ignore[import]
        token = keyring.get_password("artha", _DEFAULT_CREDENTIAL_KEY)
        if token:
            return token
    except Exception:
        pass
    return os.environ.get("ARTHA_SLACK_BOT_TOKEN") or None


def _post_message(token: str, channel: str, text: str,
                  thread_ts: str = "", blocks: str = "") -> dict[str, Any]:
    """Call chat.postMessage and return parsed response dict.

    Raises RuntimeError on API error or HTTP failure.
    """
    payload: dict[str, Any] = {
        "channel": channel,
        "text": text[:_MAX_TEXT_LENGTH],
        "mrkdwn": True,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts
    if blocks:
        try:
            payload["blocks"] = json.loads(blocks)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"slack_send: invalid JSON in 'blocks' parameter: {exc}") from exc

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{_API_BASE}/chat.postMessage",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"slack_send: HTTP {exc.code} from chat.postMessage") from exc

    if not data.get("ok"):
        err = data.get("error", "unknown_error")
        raise RuntimeError(f"slack_send: Slack API error: {err}")
    return data


# ---------------------------------------------------------------------------
# Protocol functions
# ---------------------------------------------------------------------------

def validate(proposal: ActionProposal) -> tuple[bool, str]:
    """Check that required parameters are present and structurally valid."""
    params = proposal.parameters

    channel = str(params.get("channel", "")).strip()
    if not channel:
        return False, "Missing required parameter: 'channel'"

    text = str(params.get("text", "")).strip()
    if not text:
        return False, "Missing required parameter: 'text'"

    if len(text.encode("utf-8")) > _MAX_TEXT_LENGTH:
        return False, (
            f"Parameter 'text' exceeds Slack's {_MAX_TEXT_LENGTH}-char limit "
            f"({len(text)} chars). Split into multiple messages."
        )

    # Validate blocks JSON if provided (parse-only, no API call)
    blocks_raw = params.get("blocks", "")
    if blocks_raw:
        try:
            parsed_blocks = json.loads(blocks_raw)
            if not isinstance(parsed_blocks, list):
                return False, "Parameter 'blocks' must be a JSON array"
        except json.JSONDecodeError as exc:
            return False, f"Parameter 'blocks' is not valid JSON: {exc}"

    return True, ""


def dry_run(proposal: ActionProposal) -> ActionResult:
    """Return a formatted preview of the message without calling Slack API."""
    params = proposal.parameters
    channel = str(params.get("channel", "")).strip()
    text = str(params.get("text", "")).strip()
    thread_ts = str(params.get("thread_ts", "")).strip()
    blocks = str(params.get("blocks", "")).strip()

    preview_lines = [
        "[DRY RUN — no message sent]",
        f"  Channel : {channel}",
        f"  Text    : {text[:200]}{'…' if len(text) > 200 else ''}",
    ]
    if thread_ts:
        preview_lines.append(f"  Reply-to: {thread_ts}")
    if blocks:
        preview_lines.append(f"  Blocks  : (Block Kit payload, {len(blocks)} chars)")

    return ActionResult(
        status="success",
        message="\n".join(preview_lines),
        data={"dry_run": True, "channel": channel},
        reversible=False,
        reverse_action=None,
        undo_deadline=None,
    )


def execute(proposal: ActionProposal) -> ActionResult:
    """Send the Slack message via chat.postMessage.

    Loads the bot token from keyring. Returns ActionResult with the
    Slack message ts (timestamp) in data["ts"] for potential undo.
    """
    params = proposal.parameters
    channel = str(params.get("channel", "")).strip()
    text = str(params.get("text", "")).strip()
    thread_ts = str(params.get("thread_ts", "")).strip()
    blocks = str(params.get("blocks", "")).strip()

    token = _load_token()
    if not token:
        return ActionResult(
            status="failure",
            message=(
                f"Slack bot token not found (keyring: '{_DEFAULT_CREDENTIAL_KEY}', "
                "env: ARTHA_SLACK_BOT_TOKEN). Run scripts/setup_slack.py to configure."
            ),
            data={},
            reversible=False,
            reverse_action=None,
            undo_deadline=None,
        )

    try:
        resp = _post_message(token, channel, text, thread_ts=thread_ts, blocks=blocks)
        msg_ts = resp.get("ts", "")
        return ActionResult(
            status="success",
            message=f"Message sent to {channel} (ts: {msg_ts})",
            data={"channel": channel, "ts": msg_ts, "message_ts": msg_ts},
            reversible=False,
            reverse_action=None,
            undo_deadline=None,
        )
    except Exception as exc:
        log.error("[slack_send] execute failed for proposal %s: %s", proposal.id, exc)
        return ActionResult(
            status="failure",
            message=f"Failed to send Slack message: {exc}",
            data={},
            reversible=False,
            reverse_action=None,
            undo_deadline=None,
        )


def health_check() -> bool:
    """Verify Slack bot token is valid via auth.test. Returns True if healthy.

    Takes NO arguments (ActionHandler protocol — health_check has no params).
    Never raises.
    """
    token = _load_token()
    if not token:
        log.warning("[slack_send] health_check: no token configured")
        return False

    req = urllib.request.Request(
        f"{_API_BASE}/auth.test",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ok = bool(data.get("ok"))
        if ok:
            log.info("[slack_send] health_check OK — team: %s", data.get("team", "?"))
        else:
            log.warning("[slack_send] health_check: auth.test returned ok=false: %s",
                        data.get("error", "?"))
        return ok
    except Exception as exc:
        log.warning("[slack_send] health_check failed: %s", exc)
        return False
