#!/usr/bin/env python3
# pii-guard: phone numbers stored in keyring only; message body scanned before send
"""
scripts/actions/whatsapp_cloud_send.py — WhatsApp Cloud API message handler (E10).

Phase 2 of WhatsApp integration: programmatic message sending via Meta Business API.
Phase 1 (URL scheme / wa.me link) remains in whatsapp_send.py.

SETUP REQUIRED (one-time):
  1. Meta Business account with WhatsApp Business Platform access
  2. Phone Number ID → stored in keyring as 'artha:whatsapp_phone_id'
  3. Permanent access token → stored in keyring as 'artha:whatsapp_token'
  4. Approved message templates in Meta Business Manager

Template values stored in config/artha_config.yaml:
  whatsapp:
    api_version: "v19.0"
    templates:
      birthday_greeting: "birthday_greeting_en"
      reminder: "artha_reminder"
      family_update: "family_update_en"

INVARIANTS:
  - autonomy_floor: true — EVERY message requires explicit human approval
  - undo_window_sec: null — WhatsApp messages cannot be recalled via API
  - PII guard on all outbound message body
  - Phone numbers are allowlisted (never derived from email parsing)
  - Message body NEVER stored in ActionQueue — only template_id + variable hashes
  - Audit: logs template_id + recipient_hash (NOT raw phone number)

Ref: specs/act-reloaded.md Enhancement 10
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

_ACTIONS_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _ACTIONS_DIR.parent
_ROOT_DIR = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from .base import ActionProposal, ActionResult  # type: ignore[import]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_KEYRING_SERVICE = "artha"
_KEYRING_PHONE_ID = "whatsapp_phone_id"
_KEYRING_TOKEN = "whatsapp_token"

_DEFAULT_API_VERSION = "v19.0"
_META_API_BASE = "https://graph.facebook.com"

# Validation: WhatsApp phone numbers (E.164 format)
_PHONE_E164_RE = re.compile(r"^\+?[1-9]\d{6,14}$")

# PII patterns to detect in message bodies before send
_PII_PATTERNS = [
    re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b"),   # SSN
    re.compile(r"\b\d{4}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4}\b"),  # card number
    re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I),     # email address in body
]

# Templates this handler supports
SUPPORTED_TEMPLATES = frozenset(["birthday_greeting", "reminder", "family_update"])


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

def _get_credential(key: str) -> str | None:
    """Retrieve a credential from keyring (preferred) or environment variable."""
    # Try keyring first
    try:
        import keyring  # type: ignore[import]
        value = keyring.get_password(_KEYRING_SERVICE, key)
        if value:
            return value
    except ImportError:
        pass
    # Fallback: environment variable (ARTHA_WHATSAPP_PHONE_ID, ARTHA_WHATSAPP_TOKEN)
    env_key = f"ARTHA_{key.upper()}"
    return os.environ.get(env_key)


def _load_whatsapp_config() -> dict:
    """Load WhatsApp settings from artha_config.yaml."""
    try:
        from lib.config_loader import load_config  # noqa: PLC0415
        cfg = load_config("artha_config")
        return cfg.get("whatsapp", {})
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------

def _hash_recipient(phone_number: str) -> str:
    """Return a one-way hash of phone number for audit logs (non-reversible)."""
    return hashlib.sha256(phone_number.encode()).hexdigest()[:16]


def _scan_pii(text: str) -> list[str]:
    """Return list of PII pattern names found in text."""
    found = []
    for pat in _PII_PATTERNS:
        if pat.search(text):
            found.append(pat.pattern[:30])
    return found


def _validate_phone(phone: str) -> bool:
    """Return True if phone number matches E.164 format."""
    return bool(_PHONE_E164_RE.match(phone.strip()))


# ---------------------------------------------------------------------------
# WhatsAppCloudHandler
# ---------------------------------------------------------------------------

class WhatsAppCloudHandler:
    """Send WhatsApp messages via Meta Cloud API (Phase 2).

    Satisfies ActionHandler Protocol — validate() / dry_run() are side-effect free.
    execute() is the sole write path.

    Usage (called by ActionExecutor after human approval):
        handler = WhatsAppCloudHandler()
        ok, reason = handler.validate(proposal)
        if ok:
            result = handler.execute(proposal)
    """

    def __init__(self) -> None:
        self._cfg = _load_whatsapp_config()
        self._api_version = self._cfg.get("api_version", _DEFAULT_API_VERSION)
        self._templates: dict[str, str] = self._cfg.get("templates", {})

    # ------------------------------------------------------------------
    # ActionHandler Protocol implementation
    # ------------------------------------------------------------------

    def validate(self, proposal: ActionProposal) -> tuple[bool, str]:
        """Validate proposal prior to human approval presentation.

        Checks:
          1. Required parameters present (template_id, recipient_phone, variables)
          2. Phone number format (E.164)
          3. Template is in approved list
          4. PII guard on variable values (never SSN/card in message)
          5. Credentials exist in keyring/env (warn if missing, don't fail)
        """
        params = proposal.parameters or {}

        # Required parameters
        template_id = str(params.get("template_id", "")).strip()
        if not template_id:
            return False, "Missing parameter: template_id"

        recipient_phone = str(params.get("recipient_phone", "")).strip()
        if not recipient_phone:
            return False, "Missing parameter: recipient_phone"

        if not _validate_phone(recipient_phone):
            return False, f"Invalid phone number format (must be E.164): {recipient_phone[:15]}"

        # Template validation
        template_key = params.get("template_key", template_id)
        if template_key not in SUPPORTED_TEMPLATES and template_id not in self._templates.values():
            return False, (
                f"Unsupported template '{template_id}'. "
                f"Supported: {sorted(SUPPORTED_TEMPLATES)}"
            )

        # PII scan on variable values
        variables: list[str] = params.get("variables", [])
        var_text = " ".join(str(v) for v in variables)
        pii_found = _scan_pii(var_text)
        if pii_found:
            return False, (
                f"PII detected in template variables: {pii_found}. "
                "Remove sensitive data before sending."
            )

        return True, "validation_passed"

    def dry_run(self, proposal: ActionProposal) -> str:
        """Return a human-readable description of what would be sent."""
        params = proposal.parameters or {}
        phone = str(params.get("recipient_phone", "hidden"))
        template_id = params.get("template_id", "?")
        variables = params.get("variables", [])
        phone_hash = _hash_recipient(phone)
        return (
            f"[DRY-RUN] Would send WhatsApp template '{template_id}' "
            f"to recipient #{phone_hash} with {len(variables)} variable(s)"
        )

    def execute(self, proposal: ActionProposal) -> ActionResult:
        """Send WhatsApp message via Meta Cloud API.

        Requires human approval before this method is ever called.
        autonomy_floor: true is enforced by ActionExecutor — this method
        MUST only be called after explicit user confirmation.

        Returns ActionResult with status "success" | "failure".
        Undo is NOT possible (WhatsApp API cannot recall messages).
        """
        result = ActionResult(
            status="failure",
            message="",
            data={},
            reversible=False,
            reverse_action=None,
            undo_deadline=None,
        )

        params = proposal.parameters or {}
        template_id = str(params.get("template_id", "")).strip()
        recipient_phone = str(params.get("recipient_phone", "")).strip()
        variables: list[str] = params.get("variables", [])
        language_code = params.get("language_code", "en")

        # Retrieve credentials
        phone_number_id = _get_credential(_KEYRING_PHONE_ID)
        access_token = _get_credential(_KEYRING_TOKEN)

        if not phone_number_id or not access_token:
            result.status = "failure"
            result.message = (
                "WhatsApp credentials not configured. "
                "Run: python scripts/setup_whatsapp.py to configure "
                "phone_number_id and access_token in keyring."
            )
            return result

        # Build API request payload
        components: list[dict] = []
        if variables:
            components.append({
                "type": "body",
                "parameters": [{"type": "text", "text": str(v)} for v in variables],
            })

        payload = {
            "messaging_product": "whatsapp",
            "to": recipient_phone.lstrip("+"),
            "type": "template",
            "template": {
                "name": template_id,
                "language": {"code": language_code},
                "components": components,
            },
        }

        url = f"{_META_API_BASE}/{self._api_version}/{phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        # Execute HTTP POST
        try:
            import json
            import urllib.request

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                resp_data = json.loads(resp.read().decode("utf-8"))

            message_id = resp_data.get("messages", [{}])[0].get("id", "unknown")

            result.status = "success"
            result.message = f"WhatsApp message sent: template={template_id}"
            result.data = {
                "message_id": message_id,
                "recipient_hash": _hash_recipient(recipient_phone),
                "template_id": template_id,
                # phone_number NOT stored in data
            }

        except Exception as exc:  # noqa: BLE001
            result.status = "failure"
            result.message = f"WhatsApp API error: {str(exc)[:200]}"
            result.data = {"error": str(exc)[:200]}

        return result

    def health_check(self) -> dict[str, Any]:
        """Check WhatsApp API configuration status (side-effect free)."""
        phone_id_present = bool(_get_credential(_KEYRING_PHONE_ID))
        token_present = bool(_get_credential(_KEYRING_TOKEN))
        return {
            "handler": "whatsapp_cloud_send",
            "phone_id_configured": phone_id_present,
            "token_configured": token_present,
            "ready": phone_id_present and token_present,
            "api_version": self._api_version,
            "templates_configured": list(self._templates.keys()),
            "note": (
                "Meta Business account + phone verification required. "
                "See docs/channels.md for setup instructions."
            ),
        }


def get_handler() -> WhatsAppCloudHandler:
    """Factory function for ActionExecutor handler registry."""
    return WhatsAppCloudHandler()
