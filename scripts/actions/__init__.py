#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/actions/__init__.py — Action handler allowlist.

SECURITY: Handlers are loaded exclusively from this allowlist via
importlib.import_module(). No user-supplied module paths are ever loaded.
This prevents arbitrary code execution via a tampered actions.yaml.

Ref: specs/act.md §4.3
"""
from __future__ import annotations

# Explicit allowlist of action_type → module path.
# Any action_type not in this dict cannot be executed, regardless of actions.yaml.
_HANDLER_MAP: dict[str, str] = {
    "email_send":        "scripts.actions.email_send",
    "email_reply":       "scripts.actions.email_reply",
    "calendar_create":   "scripts.actions.calendar_create",
    "calendar_modify":   "scripts.actions.calendar_modify",
    "reminder_create":   "scripts.actions.reminder_create",
    "whatsapp_send":     "scripts.actions.whatsapp_send",
    "todo_sync":         "scripts.actions.todo_sync_action",
    "instruction_sheet": "scripts.actions.instruction_sheet",
}

__all__ = ["_HANDLER_MAP"]
