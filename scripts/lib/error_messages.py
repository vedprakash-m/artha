"""scripts/lib/error_messages.py — Human-friendly messages for internal error codes.

Used by: catch-up pipeline, cmd_ask(), channel handlers.
If an error code maps to None the failure is silent (auto-cleared internally).
"""
from __future__ import annotations

# Maps internal error codes to user-facing messages.
ERROR_MESSAGES: dict[str, str | None] = {
    "gmail_401_expired":    "I couldn't reach your Gmail. Say 'reconnect Gmail' to fix it.",
    "outlook_403_blocked":  "Outlook is blocked on this network. Try from a different connection.",
    "outlook_401_expired":  "Your Outlook connection expired. Say 'reconnect Outlook' to fix it.",
    "vault_no_key":         "Your data vault needs a quick fix. Say 'fix encryption' and I'll walk you through it.",
    "vault_stale_lock":     None,  # auto-cleared silently — user never sees this
    "icloud_dns_fail":      "I couldn't reach iCloud. Check your internet connection.",
    "workiq_auth_expired":  "Your WorkIQ connection expired. Say 'reconnect WorkIQ' and I'll walk you through it.",
    "pii_block":            "I detected sensitive information in that request and blocked it for safety. Try rephrasing without account numbers or SSNs.",
    "connector_timeout":    "One of your data sources took too long to respond. I continued with available data.",
    "python_traceback":     "Something unexpected happened. I logged the details and continued with available data.",
}


def get_message(error_code: str, fallback: str | None = None) -> str | None:
    """Return human-friendly message for an error code, or fallback if not found.

    Returns None for codes that should be handled silently.
    """
    if error_code in ERROR_MESSAGES:
        return ERROR_MESSAGES[error_code]
    return fallback
