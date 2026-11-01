#!/usr/bin/env python3
# pii-guard: ignore-file — handler; PII guard applied by ActionExecutor before this runs
"""
scripts/actions/whatsapp_send.py — Send a WhatsApp message.

Phase 1 implementation: URL-scheme fallback.
Opens wa.me/{phone}?text={encoded} URL in the system default browser so
the user can complete the send manually.  This does NOT require WhatsApp
Business API credentials.

SAFETY:
  - autonomy_floor: true — always requires human approval (per actions.yaml).
  - dry_run: returns the URL without opening it.
  - execute: opens the URL in the default browser.
  - No external API calls in Phase 1 (URL scheme only).
  - Phone numbers are formatted to E.164 with leading + stripped for wa.me.

Phase 2 (disabled): replace with WhatsApp Cloud API
  (config/actions.yaml: enabled: false for the cloud variant).

Ref: specs/act.md §8.4
"""
from __future__ import annotations

import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from actions.base import ActionProposal, ActionResult


# ---------------------------------------------------------------------------
# Required parameters
# ---------------------------------------------------------------------------

_REQUIRED_PARAMS = ("phone_number", "message")

# Maximum message length WhatsApp pre-populates in the URL
_MAX_MESSAGE_LEN = 4096


# ---------------------------------------------------------------------------
# Protocol functions
# ---------------------------------------------------------------------------

def validate(proposal: ActionProposal) -> tuple[bool, str]:
    """Check required params and message length."""
    params = proposal.parameters

    for field in _REQUIRED_PARAMS:
        if not params.get(field, "").strip():
            return False, f"Missing required parameter: '{field}'"

    phone = params.get("phone_number", "")
    # Strip non-numeric except leading +
    normalized = _normalize_phone(phone)
    if not normalized:
        return False, (
            f"Parameter 'phone_number' is not a valid phone number: '{phone}'. "
            "Expected E.164 format, e.g. +12025551234 or 12025551234"
        )

    message = params.get("message", "")
    if len(message) > _MAX_MESSAGE_LEN:
        return False, f"Message too long ({len(message)} chars; max {_MAX_MESSAGE_LEN})"

    return True, ""


def dry_run(proposal: ActionProposal) -> ActionResult:
    """Generate the wa.me URL but do not open it."""
    params = proposal.parameters
    url = _build_wa_url(params)
    recipient = params.get("recipient_name", params.get("phone_number", ""))

    return ActionResult(
        status="success",
        message=f"Preview: WhatsApp to {recipient} via {url}",
        data={
            "preview_mode": True,
            "url": url,
            "phone_number": params.get("phone_number", ""),
            "recipient_name": params.get("recipient_name", ""),
            "message_length": len(params.get("message", "")),
        },
        reversible=False,
        reverse_action=None,
    )


def execute(proposal: ActionProposal) -> ActionResult:
    """Open the wa.me URL in the system default browser.

    The user physically clicks Send in WhatsApp.  This is intentional:
    the autonomy_floor=true guard plus the physical send step provides
    two layers of confirmation.
    """
    params = proposal.parameters
    url = _build_wa_url(params)
    recipient = params.get("recipient_name", params.get("phone_number", ""))

    try:
        _open_url(url)

        return ActionResult(
            status="success",
            message=f"✅ WhatsApp URL opened for {recipient} — complete send in WhatsApp",
            data={
                "url": url,
                "phone_number": params.get("phone_number", ""),
                "recipient_name": params.get("recipient_name", ""),
                "method": "url_scheme",
                "note": "User must manually click Send in WhatsApp Web/Desktop",
            },
            reversible=False,  # Cannot recall once user clicks Send
            reverse_action=None,
        )

    except Exception as e:
        return ActionResult(
            status="failure",
            message=f"Failed to open WhatsApp URL: {e}\n\nManual link: {url}",
            data={"error": str(e), "url": url},
            reversible=False,
            reverse_action=None,
        )


def build_reverse_proposal(
    original: ActionProposal,
    result_data: dict[str, Any],
) -> ActionProposal:
    """whatsapp_send does not support undo."""
    raise NotImplementedError("whatsapp_send does not support undo")


def health_check() -> bool:
    """Phase 1: URL scheme requires no credentials — always healthy."""
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_phone(phone: str) -> str:
    """Strip spaces, dashes, parentheses.  Return digits-only (no +).

    Returns empty string if the result has fewer than 7 digits.
    """
    # Remove all non-digit chars except leading +
    stripped = phone.strip()
    if stripped.startswith("+"):
        stripped = stripped[1:]
    digits = "".join(c for c in stripped if c.isdigit())
    return digits if len(digits) >= 7 else ""


def _build_wa_url(params: dict[str, Any]) -> str:
    """Build a wa.me deep-link URL."""
    phone = _normalize_phone(params.get("phone_number", ""))
    message = params.get("message", "")
    encoded = urllib.parse.quote(message, safe="")
    return f"https://wa.me/{phone}?text={encoded}"


def _open_url(url: str) -> None:
    """Open URL in the system default browser (cross-platform)."""
    import platform
    system = platform.system()
    if system == "Windows":
        # shell=False; no user-controlled data in the args
        subprocess.run(["cmd", "/c", "start", "", url], check=True, capture_output=True)
    elif system == "Darwin":
        subprocess.run(["open", url], check=True, capture_output=True)
    else:
        # Linux / WSL
        subprocess.run(["xdg-open", url], check=True, capture_output=True)
